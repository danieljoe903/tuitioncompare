import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.models import Course, TuitionSource
from .reconcile_aberystwyth_awards import download, text


CATALOGUE_URL = "https://courses.aber.ac.uk/atoz/"

POLICY_URLS = {
    "international_fees": (
        "https://www.aber.ac.uk/en/study-with-us/"
        "international/fees-scholarships/"
    ),
    "postgraduate_fees": (
        "https://www.aber.ac.uk/en/study-with-us/"
        "fees/postgrad/fees/"
    ),
    "scholarships": (
        "https://www.aber.ac.uk/en/study-with-us/international/"
        "scholarships/global-citizens-scholarship/"
    ),
    "deposit": (
        "https://www.aber.ac.uk/en/study-with-us/"
        "international/cas-and-deposit-information/"
    ),
}


def normalise(value):
    value = value.casefold()
    value = value.replace("&", " and ").replace("/", " and ")
    value = re.sub(
        r"\b(?:bsc|ba|llb|llm|msc|ma|mba|mres|mphil|phd|bvsc)\b",
        " ",
        value,
    )
    value = re.sub(r"\b(?:hons|honours)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def page_data(main):
    content = text(main)
    heading = main.find("h1")

    intakes = sorted({
        (month.title(), int(year))
        for month, year in re.findall(
            r"(?:Available for|viewing this course for)"
            r"\s+([A-Za-z]+)\s+start\s+(\d{4})",
            content,
            flags=re.IGNORECASE,
        )
    })

    return {
        "title": text(heading) if heading else "",
        "advertised_intakes": [
            {"month": month, "year": year}
            for month, year in intakes
        ],
        "text": content,
        "headings": [
            text(item)
            for item in main.find_all(["h1", "h2", "h3", "h4"])
        ],
        "tables": [
            [
                [text(cell) for cell in row.find_all(["th", "td"])]
                for row in table.find_all("tr")
            ]
            for table in main.find_all("table")
        ],
        "links": [
            {
                "text": text(link),
                "url": urljoin(
                    "https://courses.aber.ac.uk/",
                    link["href"],
                ),
            }
            for link in main.find_all("a", href=True)
        ],
    }


class Command(BaseCommand):
    help = (
        "Inspect every active Aberystwyth course without a verified "
        "international fee. No mappings approved or database writes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--candidates-per-course",
            type=int,
            default=3,
            help="Maximum candidate pages per course; default 3.",
        )

    def handle(self, *args, **options):
        limit = options["candidates_per_course"]

        if not 1 <= limit <= 5:
            raise CommandError(
                "--candidates-per-course must be between 1 and 5."
            )

        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        directory = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aberystwyth"
            / f"remaining-review-{stamp}"
        )
        directory.mkdir(parents=True, exist_ok=True)

        courses = list(
            Course.objects.filter(
                university_id="UK002",
                is_active=True,
            )
            .order_by("course_id")
            .prefetch_related("tuitions")
        )

        sources = {
            source.tuition_id: source
            for source in TuitionSource.objects.filter(
                tuition__course__university_id="UK002",
                tuition__residency="INTL",
            )
        }

        remaining = []

        for course in courses:
            tuitions = [
                record
                for record in course.tuitions.all()
                if record.residency == "INTL"
            ]

            source = (
                sources.get(tuitions[0].pk)
                if len(tuitions) == 1 else None
            )
            raw = source.raw_fees if source else None

            verified = (
                len(tuitions) == 1
                and tuitions[0].currency == "GBP"
                and source is not None
                and bool(source.academic_year)
                and bool(source.source_url)
                and isinstance(raw, dict)
                and raw.get("verified") is True
            )

            if not verified:
                remaining.append(course)

        self.stdout.write(f"Active courses: {len(courses)}")
        self.stdout.write(
            f"Courses requiring fee review: {len(remaining)}"
        )

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        records = []
        evidence_cache = {}
        policy_evidence = {}

        with requests.Session() as session:
            session.headers["User-Agent"] = (
                "TuitionCompareSourceInspector/1.0"
            )
            session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )

            try:
                html, main = download(session, CATALOGUE_URL)
                (directory / "catalogue.html").write_bytes(html)
            except (
                requests.RequestException,
                UnicodeDecodeError,
                ValueError,
            ) as exc:
                raise CommandError(
                    f"Catalogue collection failed: {exc}"
                ) from exc

            catalogue = {}

            for link in main.find_all("a", href=True):
                url = urljoin(CATALOGUE_URL, link["href"])
                parsed = urlsplit(url)

                if (
                    parsed.scheme != "https"
                    or parsed.netloc != "courses.aber.ac.uk"
                    or parsed.query
                    or parsed.fragment
                    or not parsed.path.startswith(
                        ("/undergraduate/", "/postgraduate/")
                    )
                    or len(parsed.path.strip("/").split("/")) != 2
                ):
                    continue

                label = text(link)

                # Require a catalogue label containing an award.
                if not re.search(
                    r"\b(?:BA|BSc|LLB|LLM|MA|MSc|MBA|MRes|"
                    r"MPhil|PhD|BVSc)\b",
                    label,
                    flags=re.IGNORECASE,
                ):
                    continue

                canonical_url = (
                    f"https://courses.aber.ac.uk"
                    f"{parsed.path.rstrip('/')}/"
                )

                catalogue[canonical_url] = {
                    "catalogue_title": label,
                    "source_url": canonical_url,
                    "normalised_title": normalise(
                        re.sub(r"\([^()]*\)\s*$", "", label)
                    ),
                }

            if not catalogue:
                raise CommandError(
                    "No catalogue course links found; page structure changed."
                )

            self.stdout.write(
                f"Catalogue candidates: {len(catalogue)}"
            )

            # Preserve earlier candidate suggestions as additional evidence.
            # They remain unapproved.
            previous_candidates = {}
            root = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
            previous_paths = list(root.glob("*/course-matches.json"))

            if previous_paths:
                previous_path = max(
                    previous_paths,
                    key=lambda path: path.parent.name,
                )
                try:
                    previous = json.loads(
                        previous_path.read_text(encoding="utf-8-sig")
                    )
                    if previous.get("university_id") == "UK002":
                        previous_candidates = {
                            row["course_id"]: row.get("candidates", [])
                            for row in previous.get("records", [])
                        }
                except (OSError, ValueError, KeyError):
                    self.stderr.write(
                        "Earlier candidate report could not be read; "
                        "using the refreshed catalogue."
                    )

            for position, course in enumerate(remaining, start=1):
                self.stdout.write(
                    f"\n[{position}/{len(remaining)}] "
                    f"{course.course_id}: {course.title}"
                )

                undergraduate = course.level in ("UG", "TOPUP")
                prefix = (
                    "/undergraduate/"
                    if undergraduate else "/postgraduate/"
                )
                query = normalise(course.title)
                ranked = []

                for candidate in catalogue.values():
                    if not urlsplit(
                        candidate["source_url"]
                    ).path.startswith(prefix):
                        continue

                    score = SequenceMatcher(
                        None,
                        query,
                        candidate["normalised_title"],
                    ).ratio()

                    if score >= 0.52:
                        ranked.append({
                            **candidate,
                            "similarity": round(score, 4),
                        })

                ranked.sort(
                    key=lambda item: item["similarity"],
                    reverse=True,
                )

                selected = {}

                # Prefer previously identified official candidates,
                # but retain them only if still in the refreshed catalogue.
                for candidate in previous_candidates.get(
                    course.course_id, []
                ):
                    url = candidate.get("source_url", "").rstrip("/") + "/"

                    if url in catalogue:
                        selected[url] = {
                            **catalogue[url],
                            "origin": "previous_candidate",
                        }

                for candidate in ranked:
                    if len(selected) >= limit:
                        break
                    selected.setdefault(
                        candidate["source_url"],
                        {**candidate, "origin": "title_similarity"},
                    )

                selected_items = list(selected.values())[:limit]
                candidates = []

                for candidate in selected_items:
                    url = candidate["source_url"]

                    if url not in evidence_cache:
                        self.stdout.write(f"  Reading: {url}")

                        try:
                            html, course_main = download(session, url)
                            digest = hashlib.sha256(html).hexdigest()
                            filename = (
                                hashlib.sha256(
                                    url.encode("utf-8")
                                ).hexdigest()[:20]
                                + ".html"
                            )
                            snapshot_path = directory / filename
                            snapshot_path.write_bytes(html)

                            evidence_cache[url] = {
                                "status": "collected",
                                "source_url": url,
                                "sha256": digest,
                                "html_snapshot": str(snapshot_path),
                                **page_data(course_main),
                            }

                        except (
                            requests.RequestException,
                            UnicodeDecodeError,
                            ValueError,
                        ) as exc:
                            evidence_cache[url] = {
                                "status": "request_or_content_review",
                                "source_url": url,
                                "error": str(exc),
                            }

                    candidates.append({
                        **candidate,
                        "approved": False,
                        "evidence": evidence_cache[url],
                    })

                records.append({
                    "course_id": course.course_id,
                    "stored": {
                        "title": course.title,
                        "level": course.level,
                        "duration": course.duration,
                        "start_month": course.start_month,
                        "start_year": course.start_year,
                        "course_url": course.course_url,
                        "ucas_cao_code": course.ucas_cao_code,
                        "work_placement": course.work_placement,
                    },
                    "status": (
                        "candidates_require_review"
                        if candidates else "no_catalogue_candidate"
                    ),
                    "approved": False,
                    "candidates": candidates,
                })

            for name, url in POLICY_URLS.items():
                self.stdout.write(f"\nReading policy: {url}")

                try:
                    html, policy_main = download(session, url)
                    filename = f"policy-{name}.html"
                    (directory / filename).write_bytes(html)

                    policy_evidence[name] = {
                        "status": "collected",
                        "source_url": url,
                        "sha256": hashlib.sha256(html).hexdigest(),
                        "html_snapshot": str(directory / filename),
                        **page_data(policy_main),
                    }

                except (
                    requests.RequestException,
                    UnicodeDecodeError,
                    ValueError,
                ) as exc:
                    policy_evidence[name] = {
                        "status": "needs_review",
                        "source_url": url,
                        "error": str(exc),
                    }

        report = {
            "generated_at": now.isoformat(),
            "university_id": "UK002",
            "database_changes": False,
            "mappings_approved": False,
            "active_courses": len(courses),
            "remaining_courses": len(remaining),
            "courses_with_candidates": sum(
                bool(row["candidates"]) for row in records
            ),
            "courses_without_candidates": sum(
                not row["candidates"] for row in records
            ),
            "unique_pages_collected": sum(
                page["status"] == "collected"
                for page in evidence_cache.values()
            ),
            "records": records,
            "policies": policy_evidence,
            "display_notes": {
                "scholarship_unconfirmed": (
                    "Scholarship information is not confirmed for this "
                    "course and intake. No discount has been included."
                ),
                "deposit_amount_unknown": (
                    "Deposit amount is not available for comparison. "
                    "Check your university offer letter."
                ),
            },
        }

        output = directory / "remaining-course-review.json"
        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        self.stdout.write(
            f"\nRemaining courses inspected: {len(records)}"
        )
        self.stdout.write(
            f"With candidates: {report['courses_with_candidates']}"
        )
        self.stdout.write(
            f"Without candidates: {report['courses_without_candidates']}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Candidates only. No mappings approved. "
            "No database records changed."
        )