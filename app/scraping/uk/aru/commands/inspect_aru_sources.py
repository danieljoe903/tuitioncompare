import hashlib
import json
import re
import time
from difflib import SequenceMatcher
from urllib.parse import urldefrag, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pathlib import Path

from app.models import Course


BASE_URL = "https://www.aru.ac.uk"
CATALOGUE_URL = BASE_URL + "/study/course-search"

POLICY_URLS = {
    "international_fees": (
        BASE_URL + "/international/fees-and-scholarships"
    ),
    "scholarships": (
        BASE_URL + "/international/international-scholarships"
    ),
    "undergraduate_scholarship": (
        BASE_URL
        + "/international/international-scholarships/"
        "international-undergraduate-scholarship"
    ),
    "postgraduate_scholarship": (
        BASE_URL
        + "/international/international-scholarships/"
        "international-postgraduate-scholarship"
    ),
    "deposit": (
        BASE_URL
        + "/international/how-to-apply/"
        "step-4---receive-our-decision-on-your-application"
    ),
}

COURSE_PREFIXES = (
    "/study/undergraduate/",
    "/study/postgraduate/",
    "/study/postgraduate-research/",
    "/research/postgraduate-research/",
)


def clean_text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def normalise_title(value):
    value = value.casefold()
    value = re.sub(
        r"\b(bsc|ba|beng|llb|msc|ma|mba|llm|mphil|phd|"
        r"mres|mph|mbchb|hons|honours)\b",
        " ",
        value,
    )
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def official_url(value, base=BASE_URL):
    absolute = urldefrag(urljoin(base, value))[0]
    parts = urlsplit(absolute)

    if (
        parts.scheme != "https"
        or parts.hostname not in {"aru.ac.uk", "www.aru.ac.uk"}
    ):
        return None

    return absolute


def course_url(value):
    parts = urlsplit(value)

    if parts.path.startswith(COURSE_PREFIXES):
        return parts._replace(query="", fragment="").geturl()

    return None


class Command(BaseCommand):
    help = (
        "Collect official ARU catalogue and policy evidence. "
        "Suggest course candidates without approving mappings."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-pages",
            type=int,
            default=100,
            help="Safety limit for catalogue pagination.",
        )

    def get_page(self, session, url):
        self.stdout.write(f"Reading: {url}")
        self.stdout.flush()

        response = session.get(url, timeout=(30, 90))
        response.raise_for_status()

        if official_url(response.url) is None:
            raise ValueError(
                f"Redirected outside the ARU website: {response.url}"
            )

        soup = BeautifulSoup(response.content, "html.parser")

        if soup.find("h1") is None:
            raise ValueError("Page has no main heading.")

        time.sleep(0.4)
        return response, soup

    def evidence(self, response, soup):
        main = soup.find("main") or soup

        for unwanted in main.find_all(
            ["script", "style", "nav", "footer"]
        ):
            unwanted.decompose()

        tables = []

        for table in main.find_all("table"):
            rows = []

            for row in table.find_all("tr"):
                cells = row.find_all(["th", "td"])
                if cells:
                    rows.append([
                        clean_text(cell) for cell in cells
                    ])

            if rows:
                tables.append(rows)

        return {
            "source_url": response.url,
            "checked_at": timezone.now().isoformat(),
            "sha256": hashlib.sha256(
                response.content
            ).hexdigest(),
            "title": clean_text(soup.find("h1")),
            "text": clean_text(main),
            "tables": tables,
            "links": [
                {
                    "text": clean_text(link),
                    "url": urljoin(response.url, link["href"]),
                }
                for link in main.find_all("a", href=True)
            ],
        }

    def handle(self, *args, **options):
        if options["max_pages"] < 1:
            raise CommandError("--max-pages must be positive.")

        courses = list(
            Course.objects.filter(
                university_id="UK003",
                is_active=True,
            )
            .order_by("course_id")
            .values(
                "course_id",
                "title",
                "level",
                "duration",
                "location",
                "start_month",
                "start_year",
                "course_url",
            )
        )

        if not courses:
            raise CommandError("No active UK003 courses found.")

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        folder = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aru"
            / stamp
        )
        folder.mkdir(parents=True, exist_ok=False)

        report = {
            "university_id": "UK003",
            "generated_at": timezone.now().isoformat(),
            "database_changes": False,
            "mappings_approved": False,
            "policies": {},
            "catalogue": [],
            "course_candidates": [],
            "errors": [],
        }

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        with requests.Session() as session:
            session.headers.update({
                "User-Agent": "TuitionCompareSourceInspector/1.0",
            })
            session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )

            for name, url in POLICY_URLS.items():
                try:
                    response, soup = self.get_page(session, url)
                    report["policies"][name] = self.evidence(
                        response, soup
                    )
                    (folder / f"{name}.html").write_bytes(
                        response.content
                    )
                except (requests.RequestException, ValueError) as exc:
                    report["errors"].append({
                        "source": name,
                        "url": url,
                        "reason": str(exc),
                    })

            catalogue = {}
            visited = set()
            next_url = CATALOGUE_URL

            while next_url:
                if next_url in visited:
                    report["errors"].append({
                        "source": "catalogue",
                        "reason": "Pagination cycle detected.",
                        "url": next_url,
                    })
                    break

                if len(visited) >= options["max_pages"]:
                    report["errors"].append({
                        "source": "catalogue",
                        "reason": "Catalogue page safety limit reached.",
                    })
                    break

                visited.add(next_url)

                try:
                    response, soup = self.get_page(
                        session, next_url
                    )
                except (requests.RequestException, ValueError) as exc:
                    report["errors"].append({
                        "source": "catalogue",
                        "url": next_url,
                        "reason": str(exc),
                    })
                    break

                (folder / f"catalogue-{len(visited):03}.html").write_bytes(
                    response.content
                )

                main = soup.find("main") or soup

                for link in main.find_all("a", href=True):
                    absolute = official_url(
                        link["href"], response.url
                    )
                    if not absolute:
                        continue

                    target = course_url(absolute)
                    label = clean_text(link)

                    if target and label:
                        existing = catalogue.get(target)

                        if (
                            existing is None
                            or len(label) < len(existing["title"])
                        ):
                            catalogue[target] = {
                                "title": label,
                                "source_url": target,
                            }

                following = set()

                for link in soup.find_all("a", href=True):
                    label = clean_text(link).casefold()
                    aria = (
                        link.get("aria-label", "").strip().casefold()
                    )
                    rel = link.get("rel", [])

                    if not (
                        "next" in rel
                        or label in {"next", "next page", "next ›", "next »"}
                        or aria in {"next", "next page"}
                    ):
                        continue

                    target = official_url(
                        link["href"], response.url
                    )

                    if (
                        target
                        and urlsplit(target).path.rstrip("/")
                        == "/study/course-search"
                    ):
                        following.add(target)

                if len(following) > 1:
                    report["errors"].append({
                        "source": "catalogue",
                        "reason": "Conflicting next-page links.",
                        "urls": sorted(following),
                    })
                    break

                next_url = next(iter(following), None)

        report["catalogue"] = sorted(
            catalogue.values(),
            key=lambda item: item["title"].casefold(),
        )
        report["catalogue_pages_read"] = len(visited)

        for course in courses:
            wanted = normalise_title(course["title"])
            candidates = []

            for entry in report["catalogue"]:
                candidate_title = normalise_title(
                    entry["title"]
                )
                score = SequenceMatcher(
                    None, wanted, candidate_title
                ).ratio()

                if score >= 0.60:
                    candidates.append({
                        **entry,
                        "similarity": round(score, 4),
                        "approved": False,
                    })

            candidates.sort(
                key=lambda item: item["similarity"],
                reverse=True,
            )

            report["course_candidates"].append({
                "stored_course": course,
                "candidates": candidates[:5],
                "review_required": (
                    "Confirm award, route, campus, duration, "
                    "intake and fee year before importing."
                ),
            })

        output = folder / "source-review.json"
        output.write_text(
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        matched = sum(
            bool(row["candidates"])
            for row in report["course_candidates"]
        )

        self.stdout.write("")
        self.stdout.write(f"Database courses: {len(courses)}")
        self.stdout.write(
            f"Catalogue pages read: {len(visited)}"
        )
        self.stdout.write(
            f"Catalogue course links: {len(catalogue)}"
        )
        self.stdout.write(
            f"Courses with candidates: {matched}"
        )
        self.stdout.write(
            f"Courses without candidates: {len(courses) - matched}"
        )
        self.stdout.write(
            f"Policy pages collected: {len(report['policies'])}"
        )
        self.stdout.write(
            f"Collection issues: {len(report['errors'])}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Candidates only. No mappings approved. "
            "No database records changed."
        )