import hashlib
import json
import re
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import Course
from app.scraping.uk.aberystwyth.commands.reconcile_aberystwyth_awards import download, text


# Candidate routes only. This command does not approve mappings.
CANDIDATES = {
    "C_ABE002_01": (
        "https://courses.aber.ac.uk/undergraduate/"
        "N12T-business-topup/"
    ),
    "C_UK002_23": (
        "https://courses.aber.ac.uk/postgraduate/"
        "business-administration/"
    ),
    "C_UK002_45": (
        "https://courses.aber.ac.uk/postgraduate/"
        "global-management/"
    ),
    "C_UK002_54": (
        "https://courses.aber.ac.uk/undergraduate/"
        "computer-science-and-mathematics-degree/"
    ),
    "C_UK002_60": (
        "https://courses.aber.ac.uk/undergraduate/"
        "MM91-law-and-criminology/"
    ),
}


class Command(BaseCommand):
    help = (
        "Collect official evidence for five remaining Aberystwyth "
        "course candidates. No database changes."
    )

    def handle(self, *args, **options):
        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        directory = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aberystwyth"
            / f"next-batch-{stamp}"
        )
        directory.mkdir(parents=True, exist_ok=True)

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        pages = []

        with requests.Session() as session:
            session.headers["User-Agent"] = (
                "TuitionCompareSourceInspector/1.0"
            )
            session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )

            for course_id, url in CANDIDATES.items():
                course = Course.objects.filter(
                    pk=course_id,
                    university_id="UK002",
                    is_active=True,
                ).first()

                record = {
                    "course_id": course_id,
                    "source_url": url,
                    "approved": False,
                }

                if course is None:
                    record.update({
                        "status": "needs_review",
                        "error": "Active database course not found.",
                    })
                    pages.append(record)
                    continue

                record["stored"] = {
                    "title": course.title,
                    "level": course.level,
                    "duration": course.duration,
                    "start_month": course.start_month,
                    "start_year": course.start_year,
                    "course_url": course.course_url,
                    "ucas_cao_code": course.ucas_cao_code,
                    "work_placement": course.work_placement,
                }

                self.stdout.write(f"Reading {course_id}: {url}")

                try:
                    html, main = download(session, url)
                    heading = main.find("h1")

                    if heading is None:
                        raise ValueError("Official course heading missing.")

                    content = text(main)

                    intake_matches = re.findall(
                        r"(?:Available for|viewing this course for)"
                        r"\s+([A-Za-z]+)\s+start\s+(\d{4})",
                        content,
                        flags=re.IGNORECASE,
                    )

                    intakes = [
                        {"month": month.title(), "year": year}
                        for month, year in sorted({
                            (month, int(year))
                            for month, year in intake_matches
                        })
                    ]

                    if not intakes:
                        raise ValueError(
                            "Advertised intake could not be extracted."
                        )

                    snapshot_path = directory / f"{course_id}.html"
                    snapshot_path.write_bytes(html)

                    record.update({
                        "status": "collected",
                        "title": text(heading),
                        "advertised_intakes": intakes,
                        "text": content,
                        "headings": [
                            text(item)
                            for item in main.find_all(
                                ["h1", "h2", "h3", "h4"]
                            )
                        ],
                        "links": [
                            {
                                "text": text(link),
                                "href": link["href"],
                            }
                            for link in main.find_all("a", href=True)
                        ],
                        "tables": [
                            [
                                [
                                    text(cell)
                                    for cell in row.find_all(["th", "td"])
                                ]
                                for row in table.find_all("tr")
                            ]
                            for table in main.find_all("table")
                        ],
                        "html_snapshot": str(snapshot_path),
                        "sha256": hashlib.sha256(html).hexdigest(),
                    })

                    self.stdout.write(
                        f"Collected: {record['title']} — {intakes}"
                    )

                except (
                    requests.RequestException,
                    UnicodeDecodeError,
                    ValueError,
                ) as exc:
                    record.update({
                        "status": "needs_review",
                        "error": str(exc),
                    })
                    self.stderr.write(
                        f"{course_id}: review required — {exc}"
                    )

                pages.append(record)

        collected = sum(
            page["status"] == "collected" for page in pages
        )

        report = {
            "generated_at": now.isoformat(),
            "university_id": "UK002",
            "database_changes": False,
            "mappings_approved": False,
            "pages_attempted": len(pages),
            "pages_collected": collected,
            "pages_needing_review": len(pages) - collected,
            "pages": pages,
        }

        output = directory / "course-evidence.json"
        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        self.stdout.write(f"\nCollected: {collected}/{len(pages)}")
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "No mappings approved. No database records changed."
        )