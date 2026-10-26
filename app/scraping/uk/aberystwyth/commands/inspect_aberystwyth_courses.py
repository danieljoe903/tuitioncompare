import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.models import Course


def clean_text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def normalise(value):
    value = value.casefold().replace("&", " and ")
    return " ".join(
        re.sub(r"[^a-z0-9]+", " ", value).split()
    )


def read_json(path):
    try:
        return json.loads(
            path.read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError) as exc:
        raise CommandError(
            f"Cannot read {path}: {exc}"
        ) from exc


def extract_page(html, response_url):
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")

    if heading is None:
        raise ValueError("Course heading missing.")

    title = clean_text(heading)

    if not title:
        raise ValueError("Course heading is empty.")

    main = (
        soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.body
    )

    if main is None:
        raise ValueError("Course content missing.")

    for element in main.find_all(
        ["script", "style", "nav", "footer", "noscript"]
    ):
        element.decompose()

    content = clean_text(main)

    intakes = sorted(set(
        re.findall(
            r"(?:Available for|viewing this course for)"
            r"\s+([A-Za-z]+)\s+start\s+(20\d{2})",
            content,
            flags=re.IGNORECASE,
        )
    ))

    links = []
    seen = set()

    for link in main.find_all("a", href=True):
        url = urljoin(response_url, link["href"])
        parsed = urlsplit(url)

        if (
            parsed.scheme != "https"
            or parsed.hostname not in (
                "www.aber.ac.uk",
                "aber.ac.uk",
                "courses.aber.ac.uk",
            )
        ):
            continue

        label = clean_text(link)
        key = (label, url)

        if key in seen:
            continue

        seen.add(key)
        links.append({"text": label, "url": url})

    tables = []

    for table in main.find_all("table"):
        rows = []

        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])

            if cells:
                rows.append([
                    clean_text(cell)
                    for cell in cells
                ])

        if rows:
            tables.append(rows)

    return {
        "title": title,
        "page_title": (
            clean_text(soup.title)
            if soup.title
            else ""
        ),
        "advertised_intakes": [
            {"month": month, "year": int(year)}
            for month, year in intakes
        ],
        "text": content,
        "links": links,
        "tables": tables,
    }


class Command(BaseCommand):
    help = (
        "Collect course-page evidence for unique Aberystwyth "
        "catalogue candidates. No database updates."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--matches",
            type=Path,
            help="Path to an existing course-matches.json report.",
        )

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        base_directory = root / "logs" / "aberystwyth"

        matches_path = options["matches"]

        if matches_path is None:
            paths = list(
                base_directory.glob("*/course-matches.json")
            )

            if not paths:
                raise CommandError(
                    "No course-matches.json found. Run "
                    "preview_aberystwyth_matches first."
                )

            matches_path = max(
                paths,
                key=lambda path: path.parent.name,
            )

        matches = read_json(matches_path)

        if matches.get("university_id") != "UK002":
            raise CommandError(
                "The match report is not for Aberystwyth."
            )

        records = matches.get("records")

        if not isinstance(records, list):
            raise CommandError("Invalid match-report records.")

        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")

        directory = base_directory / stamp
        directory.mkdir(parents=True, exist_ok=True)

        courses = {
            course.course_id: course
            for course in Course.objects.filter(
                university_id="UK002",
                is_active=True,
            )
        }

        report = {
            "generated_at": now.isoformat(),
            "university_id": "UK002",
            "database_changes": False,
            "matches_file": str(matches_path),
            "pages": [],
            "held": [],
        }

        retry = Retry(
            total=3,
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

            for row in records:
                course = courses.get(row["course_id"])

                if course is None:
                    continue

                candidates = [
                    candidate
                    for candidate in row.get("candidates", [])
                    if not any(
                        flag in candidate.get("flags", [])
                        for flag in (
                            "award_mismatch",
                            "duration_mismatch",
                        )
                    )
                ]

                if len(candidates) != 1:
                    report["held"].append({
                        "course_id": course.course_id,
                        "title": course.title,
                        "reason": (
                            "No unique candidate matching "
                            "title, award and duration."
                        ),
                    })
                    continue

                # Do not collapse a research MSc into a taught MSc.
                if re.search(
                    r"\(\s*research\s*\)|\bby research\b",
                    course.title,
                    flags=re.IGNORECASE,
                ):
                    report["held"].append({
                        "course_id": course.course_id,
                        "title": course.title,
                        "reason": (
                            "Research-route identity needs "
                            "separate confirmation."
                        ),
                    })
                    continue

                candidate = candidates[0]
                url = candidate["source_url"]
                parsed = urlsplit(url)

                if (
                    parsed.scheme != "https"
                    or parsed.hostname != "courses.aber.ac.uk"
                ):
                    raise CommandError(
                        f"Unexpected candidate URL: {url}"
                    )

                page = {
                    "course_id": course.course_id,
                    "stored_title": course.title,
                    "stored_level": course.level,
                    "stored_duration": course.duration,
                    "stored_intake": {
                        "month": course.start_month,
                        "year": course.start_year,
                    },
                    "catalogue_candidate": candidate,
                    "source_url": url,
                    "approved": False,
                }

                self.stdout.write(
                    f"Reading {course.course_id}: {url}"
                )

                try:
                    response = session.get(
                        url,
                        timeout=(30, 60),
                    )
                    response.raise_for_status()

                    final = urlsplit(response.url)

                    if (
                        final.scheme != "https"
                        or final.hostname != "courses.aber.ac.uk"
                        or final.path.rstrip("/")
                        != parsed.path.rstrip("/")
                        or final.query != parsed.query
                    ):
                        raise ValueError(
                            f"Unexpected redirect: {response.url}"
                        )

                    html = response.content.decode("utf-8-sig")
                    extracted = extract_page(
                        html,
                        response.url,
                    )

                    if (
                        normalise(extracted["title"])
                        != normalise(candidate["subject_title"])
                    ):
                        raise ValueError(
                            "Course-page title differs from "
                            "the catalogue candidate."
                        )

                    snapshot = directory / (
                        f"{course.course_id}.html"
                    )
                    snapshot.write_bytes(response.content)

                    intake_matches = any(
                        intake["year"] == course.start_year
                        and intake["month"].casefold()
                        == course.start_month.casefold()
                        for intake in extracted["advertised_intakes"]
                    )

                    page.update(extracted)
                    page.update({
                        "response_url": response.url,
                        "status": "collected",
                        "stored_intake_advertised": intake_matches,
                        "html_snapshot": str(snapshot),
                        "sha256": hashlib.sha256(
                            response.content
                        ).hexdigest(),
                        "review_note": (
                            "Intake found; award, route and fee band "
                            "still require verification."
                            if intake_matches
                            else
                            "Stored intake was not found in the "
                            "recognised page wording. Review required."
                        ),
                    })

                except (
                    requests.RequestException,
                    UnicodeDecodeError,
                    ValueError,
                ) as exc:
                    page["status"] = "needs_review"
                    page["error"] = str(exc)

                    self.stderr.write(
                        f"Needs review: {exc}"
                    )

                report["pages"].append(page)
                time.sleep(1)

        report["summary"] = {
            "pages_attempted": len(report["pages"]),
            "pages_collected": sum(
                page["status"] == "collected"
                for page in report["pages"]
            ),
            "pages_needing_review": sum(
                page["status"] != "collected"
                for page in report["pages"]
            ),
            "stored_intake_advertised": sum(
                page.get("stored_intake_advertised") is True
                for page in report["pages"]
            ),
            "held_courses": len(report["held"]),
        }

        output = directory / "course-evidence.json"

        output.write_text(
            json.dumps(
                report,
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
            "No mappings approved. No database records changed."
        )