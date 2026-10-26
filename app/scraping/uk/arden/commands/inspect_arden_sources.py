import hashlib
import json
import re
from collections import deque
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import (
    parse_qs,
    urldefrag,
    urljoin,
    urlparse,
)

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from app.models import (
    Course,
    DepositPolicy,
    Scholarship,
    TuitionSource,
)


START_URLS = [
    "https://arden.ac.uk/international-students/find-a-course",
    "https://arden.ac.uk/our-courses",
    (
        "https://arden.ac.uk/international-students/"
        "fees-for-international-students"
    ),
    (
        "https://arden.ac.uk/international-students/"
        "scholarships-funding"
    ),
    (
        "https://arden.ac.uk/studying-with-us/fees-and-funding/"
        "all-scholarships"
    ),
    "https://arden.ac.uk/terms-and-conditions",
]

CATALOGUE_PATHS = {
    "/international-students/find-a-course",
    "/our-courses",
}

ARU_FOLLOWUP_IDS = [
    "C_UK003_01",
    "C_UK003_05",
    "C_UK003_31",
    "C_UK003_36",
    "C_UK003_95",
]


def normalise(value):
    value = value.casefold()
    value = re.sub(
        r"\b(?:bsc|ba|beng|llb|llm|msc|ma|mba|hons)\b",
        " ",
        value,
    )
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


class Command(BaseCommand):
    help = (
        "Collect Arden catalogue and policy evidence. "
        "No mappings approved or database updates applied."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-pages",
            type=int,
            default=30,
            help="Maximum source/catalogue pages to collect.",
        )

    @staticmethod
    def allowed(url):
        parsed = urlparse(url)

        return (
            parsed.scheme == "https"
            and parsed.hostname in {"arden.ac.uk", "www.arden.ac.uk"}
            and parsed.username is None
            and parsed.password is None
        )

    def fetch(self, session, url):
        current = url
        redirects = []

        for _ in range(6):
            if not self.allowed(current):
                raise ValueError(
                    f"Unsupported source URL: {current}"
                )

            response = session.get(
                current,
                timeout=(30, 60),
                allow_redirects=False,
            )

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")

                if not location:
                    raise ValueError(
                        "Redirect has no Location header."
                    )

                destination = urljoin(current, location)

                redirects.append({
                    "from": current,
                    "to": destination,
                })

                current = destination
                continue

            response.raise_for_status()

            if "html" not in response.headers.get(
                "Content-Type", ""
            ).casefold():
                raise ValueError("Expected an HTML source page.")

            return response, current, redirects

        raise ValueError("Too many redirects.")

    def handle(self, *args, **options):
        max_pages = options["max_pages"]

        if max_pages < len(START_URLS):
            self.stderr.write(
                f"--max-pages must be at least {len(START_URLS)}."
            )
            return

        courses = list(
            Course.objects.filter(
                university_id="UK004"
            ).order_by("course_id").values()
        )

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        folder = (
            Path(settings.BASE_DIR)
            / "logs"
            / "arden"
            / f"source-review-{stamp}"
        )
        folder.mkdir(parents=True, exist_ok=True)

        pages = []
        errors = []
        course_links = {}
        queue = deque(START_URLS)
        queued = set(START_URLS)
        visited = set()

        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )

        with requests.Session() as session:
            session.headers["User-Agent"] = (
                "TuitionCompareSourceInspector/1.0"
            )
            session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )

            while queue and len(visited) < max_pages:
                url = queue.popleft()

                if url in visited:
                    continue

                visited.add(url)
                self.stdout.write(f"Reading: {url}")

                try:
                    response, final_url, redirects = self.fetch(
                        session,
                        url,
                    )

                    html_path = (
                        folder / f"page-{len(visited):03d}.html"
                    )
                    html_path.write_bytes(response.content)

                    soup = BeautifulSoup(
                        response.content,
                        "html.parser",
                    )

                    links = []

                    for link in soup.find_all("a", href=True):
                        href = urldefrag(
                            urljoin(final_url, link["href"])
                        )[0]

                        label = " ".join(
                            link.get_text(" ", strip=True).split()
                        )

                        links.append({
                            "text": label,
                            "href": href,
                        })

                        if not self.allowed(href):
                            continue

                        parsed = urlparse(href)
                        path = parsed.path.rstrip("/")
                        query = parse_qs(parsed.query)

                        # Follow advertised pagination only.
                        if (
                            path in CATALOGUE_PATHS
                            and "page" in query
                            and href not in queued
                        ):
                            queued.add(href)
                            queue.append(href)

                        # These are candidate links, not approved
                        # course identities or campus mappings.
                        if (
                            re.match(
                                r"^/our-courses/"
                                r"(undergraduate|postgraduate)/",
                                path,
                            )
                            and label
                        ):
                            clean_url = href.split("?", 1)[0]

                            course_links.setdefault(
                                clean_url,
                                {
                                    "url": clean_url,
                                    "labels": [],
                                },
                            )

                            if label not in course_links[
                                clean_url
                            ]["labels"]:
                                course_links[clean_url][
                                    "labels"
                                ].append(label)

                    scripts = []

                    for script in soup.find_all("script"):
                        text = (
                            script.string or script.get_text()
                        )

                        if not text.strip():
                            continue

                        parsed_json = None

                        try:
                            parsed_json = json.loads(text)
                        except (ValueError, TypeError):
                            pass

                        scripts.append({
                            "id": script.get("id"),
                            "type": script.get("type"),
                            "text": text,
                            "parsed_json": parsed_json,
                        })

                    tables = []

                    for table in soup.find_all("table"):
                        rows = []

                        for row in table.find_all("tr"):
                            cells = row.find_all(["th", "td"])

                            if cells:
                                rows.append([
                                    " ".join(
                                        cell.get_text(
                                            " ", strip=True
                                        ).split()
                                    )
                                    for cell in cells
                                ])

                        if rows:
                            tables.append(rows)

                    heading = soup.find("h1")

                    for element in soup.find_all(
                        ["script", "style"]
                    ):
                        element.decompose()

                    pages.append({
                        "requested_url": url,
                        "source_url": final_url,
                        "redirects": redirects,
                        "heading": (
                            heading.get_text(" ", strip=True)
                            if heading else ""
                        ),
                        "sha256": hashlib.sha256(
                            response.content
                        ).hexdigest(),
                        "html_file": str(html_path.resolve()),
                        "text": " ".join(
                            soup.get_text(
                                " ", strip=True
                            ).split()
                        ),
                        "tables": tables,
                        "links": links,
                        "scripts": scripts,
                    })

                except (
                    requests.RequestException,
                    OSError,
                    ValueError,
                ) as exc:
                    errors.append({
                        "requested_url": url,
                        "reason": str(exc),
                    })

        matches = []

        for course in courses:
            title = normalise(course["title"])
            candidates = []

            for item in course_links.values():
                best = max(
                    (
                        SequenceMatcher(
                            None,
                            title,
                            normalise(label),
                        ).ratio()
                        for label in item["labels"]
                    ),
                    default=0,
                )

                if best >= 0.60:
                    candidates.append({
                        **item,
                        "title_similarity": round(best, 4),
                        "mapping_approved": False,
                    })

            candidates.sort(
                key=lambda item: item["title_similarity"],
                reverse=True,
            )

            matches.append({
                "database_course": course,
                "candidates": candidates[:5],
                "review_required": True,
            })

        # Read the five ARU deposit-label discrepancies without
        # changing ARU records while starting Arden.
        aru_sources = list(
            TuitionSource.objects.filter(
                tuition__course__university_id="UK003",
                tuition__course_id__in=ARU_FOLLOWUP_IDS,
                tuition__residency="INTL",
            ).values(
                "id",
                "tuition__course_id",
                "academic_year",
                "raw_fees",
            )
        )

        aru_followup = [
            {
                "course_id": row["tuition__course_id"],
                "source_id": row["id"],
                "academic_year": row["academic_year"],
                "course_kind": (
                    row["raw_fees"].get("course_kind")
                    if isinstance(row["raw_fees"], dict)
                    else None
                ),
            }
            for row in aru_sources
        ]

        report = {
            "generated_at": timezone.now().isoformat(),
            "university_id": "UK004",
            "database_changes": False,
            "mappings_approved": False,
            "summary": {
                "database_courses": len(courses),
                "pages_collected": len(pages),
                "candidate_course_links": len(course_links),
                "courses_with_candidates": sum(
                    bool(row["candidates"]) for row in matches
                ),
                "courses_without_candidates": sum(
                    not row["candidates"] for row in matches
                ),
                "collection_errors": len(errors),
                "queued_pages_not_collected": len(queue),
            },
            "courses": matches,
            "pages": pages,
            "errors": errors,
            "catalogue_collection_note": (
                "Only links exposed by captured pages were collected. "
                "Dynamic or unexposed catalogue results may be missing. "
                "No candidate does not prove discontinuation."
            ),
            "stored_scholarships": list(
                Scholarship.objects.filter(
                    university_id="UK004"
                ).values()
            ),
            "stored_deposit_policies": list(
                DepositPolicy.objects.filter(
                    university_id="UK004"
                ).values()
            ),
            "aru_deposit_label_followup": aru_followup,
        }

        output = folder / "source-review.json"

        output.write_text(
            json.dumps(
                report,
                cls=DjangoJSONEncoder,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            json.dumps(report["summary"], indent=2)
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Candidates only. No mappings approved. "
            "No database records changed."
        )