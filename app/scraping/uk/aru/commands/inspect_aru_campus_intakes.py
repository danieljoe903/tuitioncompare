import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.models import Course


COURSES = {
    "C_UK003_65": "computer-games-programming",
    "C_UK003_94": "3d-animation",
    "C_ARU003_RES_01": "../postgraduate/school-of-allied-health",
    "C_UK003_26": (
        "https://london.aru.ac.uk/courses/master-of-public-health"
    ),
}

BASE_URL = "https://www.aru.ac.uk/study/undergraduate/"


def clean_text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def permitted_url(url):
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in {"aru.ac.uk", "www.aru.ac.uk"}
        and parsed.username is None
        and parsed.password is None
    )


class Command(BaseCommand):
    help = (
        "Collect ARU course and application evidence for four "
        "campus-specific intake reviews. No database changes."
    )

    def handle(self, *args, **options):
        stamp = datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        folder = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aru"
            / f"campus-intakes-{stamp}"
        )
        folder.mkdir(parents=True, exist_ok=False)

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "university_id": "UK003",
            "database_changes": False,
            "mappings_approved": False,
            "courses": [],
            "pages": [],
            "errors": [],
        }

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        def collect(session, url):
            if not permitted_url(url):
                raise ValueError(f"Unexpected source URL: {url}")

            self.stdout.write(f"Reading: {url}")
            self.stdout.flush()

            # Validate every redirect before following it.
            current = url
            for _ in range(6):
                response = session.get(
                    current,
                    timeout=(30, 60),
                    allow_redirects=False,
                )
                if response.is_redirect:
                    current = urljoin(
                        current, response.headers["Location"]
                    )
                    if not permitted_url(current):
                        raise ValueError(
                            f"Unexpected redirect: {current}"
                        )
                    continue
                response.raise_for_status()
                break
            else:
                raise ValueError("Too many redirects.")

            if "html" not in response.headers.get(
                "Content-Type", ""
            ).casefold():
                raise ValueError("Source did not return HTML.")

            content = response.content
            digest = hashlib.sha256(content).hexdigest()
            filename = f"{len(report['pages']) + 1:02d}-{digest[:12]}.html"
            (folder / filename).write_bytes(content)

            soup = BeautifulSoup(content, "html.parser")
            scripts = []

            for script in soup.find_all("script"):
                body = script.get_text()
                item = {
                    "id": script.get("id"),
                    "type": script.get("type"),
                    "src": (
                        urljoin(current, script["src"])
                        if script.get("src")
                        else None
                    ),
                    "text": body,
                }
                try:
                    item["parsed_json"] = json.loads(body)
                except (ValueError, TypeError):
                    pass
                scripts.append(item)

            links = [
                {
                    "text": clean_text(link),
                    "url": urljoin(current, link["href"]),
                }
                for link in soup.find_all("a", href=True)
            ]

            for element in soup(["script", "style"]):
                element.decompose()

            page = {
                "requested_url": url,
                "source_url": current,
                "sha256": digest,
                "html_file": filename,
                "text": clean_text(soup),
                "links": links,
                "scripts": scripts,
            }
            report["pages"].append(page)
            return page

        with requests.Session() as session:
            session.headers["User-Agent"] = (
                "UniversityTuitionCompareSourceInspector/1.0"
            )
            session.mount(
                "https://", HTTPAdapter(max_retries=retry)
            )

            for course_id, slug in COURSES.items():
                course = Course.objects.filter(
                    university_id="UK003",
                    course_id=course_id,
                ).values(
                    "course_id",
                    "title",
                    "level",
                    "location",
                    "duration",
                    "start_month",
                    "start_year",
                    "work_placement",
                    "course_url",
                    "is_active",
                ).first()

                if course is None:
                    report["errors"].append({
                        "course_id": course_id,
                        "error": "Database course not found.",
                    })
                    continue

                entry = {
                    "database_course": course,
                    "course_source_url": urljoin(BASE_URL, slug),
                    "application_urls": [],
                    "status": "review_required",
                }
                report["courses"].append(entry)

                try:
                    page = collect(
                        session, entry["course_source_url"]
                    )

                    application_urls = sorted({
                        link["url"]
                        for link in page["links"]
                        if (
                            permitted_url(link["url"])
                            and urlparse(
                                link["url"]
                            ).path.rstrip("/")
                            == "/study/apply-online"
                        )
                    })
                    entry["application_urls"] = application_urls

                    if not application_urls:
                        report["errors"].append({
                            "course_id": course_id,
                            "error": (
                                "No application link found; "
                                "manual route review required."
                            ),
                        })

                    for url in application_urls:
                        try:
                            collect(session, url)
                        except (
                            requests.RequestException,
                            ValueError,
                            KeyError,
                        ) as exc:
                            report["errors"].append({
                                "course_id": course_id,
                                "source_url": url,
                                "error": str(exc),
                            })

                except (
                    requests.RequestException,
                    ValueError,
                    KeyError,
                ) as exc:
                    report["errors"].append({
                        "course_id": course_id,
                        "error": str(exc),
                    })

        output = folder / "campus-intake-evidence.json"
        output.write_text(
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            f"\nDatabase courses: {len(report['courses'])}"
        )
        self.stdout.write(
            f"Pages collected: {len(report['pages'])}"
        )
        self.stdout.write(
            f"Collection issues: {len(report['errors'])}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Evidence collected only. No mappings approved."
        )
        self.stdout.write("No database records changed.")

        if report["errors"]:
            raise CommandError(
                "Some evidence needs review. "
                "Details were saved in the report."
            )