import hashlib
import json
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from app.models import Course


COURSE_IDS = [
    "C_UK003_18",
    "C_UK003_22",
    "C_UK003_26",
    "C_UK003_41",
    "C_UK003_43",
    "C_UK003_46",
    "C_UK003_52",
    "C_UK003_56",
    "C_UK003_64",
    "C_UK003_66",
    "C_UK003_81",
    "C_UK003_82",
    "C_UK003_87",
    "C_UK003_88",
    "C_UK003_89",
    "C_UK003_94",
    "C_UK003_104",
]

# These are investigation candidates, not approved mappings.
CANDIDATE_URLS = {
    "C_UK003_22": (
        "https://www.aru.ac.uk/study/postgraduate/"
        "applied-data-science"
    ),
    "C_UK003_26": (
        "https://london.aru.ac.uk/courses/"
        "master-of-public-health"
    ),
    "C_UK003_66": (
        "https://www.aru.ac.uk/study/undergraduate/"
        "music-performance"
    ),
    "C_UK003_81": (
        "https://www.aru.ac.uk/study/undergraduate/"
        "politics-and-international-relations"
    ),
    "C_UK003_82": (
        "https://www.aru.ac.uk/study/undergraduate/"
        "politics-and-international-relations"
    ),
}

ALLOWED_HOSTS = {
    "www.aru.ac.uk",
    "aru.ac.uk",
    "london.aru.ac.uk",
}


class Command(BaseCommand):
    help = (
        "Collect current evidence for the 17 pending ARU courses. "
        "Does not approve mappings or modify database records."
    )

    @staticmethod
    def allowed_url(url):
        parsed = urlparse(url)

        return (
            parsed.scheme == "https"
            and parsed.hostname in ALLOWED_HOSTS
            and parsed.username is None
            and parsed.password is None
        )

    def fetch_page(self, session, requested_url, folder, index):
        current_url = requested_url
        redirects = []

        # Follow only explicitly allowed university hosts.
        for _ in range(6):
            if not self.allowed_url(current_url):
                raise ValueError(
                    f"Unsupported source URL: {current_url}"
                )

            response = session.get(
                current_url,
                timeout=(30, 60),
                allow_redirects=False,
            )

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")

                if not location:
                    raise ValueError(
                        "Redirect response has no Location header."
                    )

                next_url = urljoin(current_url, location)

                redirects.append({
                    "from": current_url,
                    "to": next_url,
                    "status_code": response.status_code,
                })

                current_url = next_url
                continue

            response.raise_for_status()
            break

        else:
            raise ValueError("Too many redirects.")

        if "html" not in response.headers.get(
            "Content-Type", ""
        ).casefold():
            raise ValueError("Source did not return HTML.")

        html_path = folder / f"page-{index:02d}.html"
        html_path.write_bytes(response.content)

        soup = BeautifulSoup(
            response.content,
            "html.parser",
        )

        scripts = []

        for script in soup.find_all("script"):
            script_text = script.string or script.get_text()

            if not script_text.strip():
                continue

            parsed_json = None

            try:
                parsed_json = json.loads(script_text)
            except (ValueError, TypeError):
                pass

            scripts.append({
                "id": script.get("id"),
                "type": script.get("type"),
                "src": script.get("src"),
                "text": script_text,
                "parsed_json": parsed_json,
            })

        links = [
            {
                "text": " ".join(
                    link.get_text(" ", strip=True).split()
                ),
                "href": urljoin(current_url, link["href"]),
            }
            for link in soup.find_all("a", href=True)
        ]

        for element in soup.find_all(["script", "style"]):
            element.decompose()

        heading = soup.find("h1")
        title = soup.find("title")

        return {
            "requested_url": requested_url,
            "source_url": current_url,
            "redirects": redirects,
            "redirect_review_required": bool(redirects),
            "status_code": response.status_code,
            "sha256": hashlib.sha256(
                response.content
            ).hexdigest(),
            "html_file": str(html_path.resolve()),
            "page_title": (
                title.get_text(" ", strip=True)
                if title else ""
            ),
            "heading": (
                heading.get_text(" ", strip=True)
                if heading else ""
            ),
            "text": " ".join(
                soup.get_text(" ", strip=True).split()
            ),
            "links": links,
            "scripts": scripts,
        }

    def handle(self, *args, **options):
        courses = list(
            Course.objects.filter(
                university_id="UK003",
                course_id__in=COURSE_IDS,
            )
            .order_by("course_id")
            .values()
        )

        found = {course["course_id"] for course in courses}
        missing = sorted(set(COURSE_IDS) - found)

        if missing:
            raise CommandError(
                "Missing database courses: " + ", ".join(missing)
            )

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        folder = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aru"
            / f"remaining-17-evidence-{stamp}"
        )
        folder.mkdir(parents=True, exist_ok=True)

        report = {
            "generated_at": timezone.now().isoformat(),
            "university_id": "UK003",
            "database_changes": False,
            "mappings_approved": False,
            "courses": [],
            "pages": [],
            "errors": [],
        }

        requested_urls = []

        for course in courses:
            course_id = course["course_id"]
            url = CANDIDATE_URLS.get(course_id)

            if not url:
                stored_url = course.get("course_url") or ""

                if self.allowed_url(stored_url):
                    url = stored_url

            status = (
                "candidate_capture_required"
                if url
                else "current_route_not_identified"
            )

            report["courses"].append({
                "database_course": course,
                "candidate_url": url,
                "status": status,
                "mapping_approved": False,
            })

            if url and url not in requested_urls:
                requested_urls.append(url)

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

            for index, url in enumerate(requested_urls, start=1):
                self.stdout.write(f"Reading: {url}")

                try:
                    page = self.fetch_page(
                        session,
                        url,
                        folder,
                        index,
                    )
                    report["pages"].append(page)

                except (
                    requests.RequestException,
                    OSError,
                    ValueError,
                ) as exc:
                    report["errors"].append({
                        "requested_url": url,
                        "reason": str(exc),
                    })
                    self.stderr.write(
                        f"Collection issue: {url}: {exc}"
                    )

        captured = {
            page["requested_url"]: page
            for page in report["pages"]
        }

        for entry in report["courses"]:
            url = entry["candidate_url"]

            if not url:
                continue

            page = captured.get(url)

            if page is None:
                entry["status"] = "collection_failed"

            elif page["redirect_review_required"]:
                entry["status"] = "redirect_requires_review"

            else:
                entry["status"] = "candidate_evidence_collected"

        report["summary"] = {
            "database_courses": len(courses),
            "unique_candidate_urls": len(requested_urls),
            "pages_collected": len(report["pages"]),
            "courses_without_current_route": sum(
                entry["candidate_url"] is None
                for entry in report["courses"]
            ),
            "redirected_pages": sum(
                page["redirect_review_required"]
                for page in report["pages"]
            ),
            "collection_errors": len(report["errors"]),
        }

        output = folder / "remaining-17-evidence.json"

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
            "Evidence collected only. No mappings approved. "
            "No database records changed."
        )