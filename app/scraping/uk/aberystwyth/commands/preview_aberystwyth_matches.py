import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.models import Course


CATALOGUE_URL = "https://courses.aber.ac.uk/atoz/"

AWARDS = (
    "BSc", "BA", "LLB", "BVSc", "BEng",
    "MSc", "MA", "MBA", "MRes", "MPhil",
    "PhD", "LLM", "MComp", "MPhys", "MBiol",
    "DProf", "DAg", "MAg", "FDSc",
)

AWARD_PATTERN = "|".join(
    sorted(AWARDS, key=len, reverse=True)
)


def clean_text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def normalise(value):
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"\(\s*hons\s*\)", " ", value)
    value = re.sub(r"\bhonours\b", " ", value)

    # Punctuation differences produce candidates only.
    # They never establish an approved identity.
    value = re.sub(r"[^a-z0-9]+", " ", value)

    return " ".join(value.split())


def stored_identity(course):
    title = course.title.strip()

    match = re.match(
        rf"^({AWARD_PATTERN})\b",
        title,
        flags=re.IGNORECASE,
    )

    if match is None:
        return {
            "award": None,
            "subject_title": title,
            "normalised_title": normalise(title),
        }

    award = match.group(1).upper()
    subject_title = title[match.end():].strip()

    subject_title = re.sub(
        r"^\(\s*hons\s*\)\s*",
        "",
        subject_title,
        flags=re.IGNORECASE,
    )

    subject_title = re.sub(
        r"^\(\s*research\s*\)\s*",
        "",
        subject_title,
        flags=re.IGNORECASE,
    )

    return {
        "award": award,
        "subject_title": subject_title,
        "normalised_title": normalise(subject_title),
    }


def duration_years(value):
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s+years?\s*",
        value or "",
        flags=re.IGNORECASE,
    )

    return match.group(1) if match else None


def parse_catalogue(html):
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")

    if heading is None or "course search" not in clean_text(heading).casefold():
        raise ValueError("Unexpected catalogue page.")

    records = {}

    for link in soup.find_all("a", href=True):
        url = urljoin(CATALOGUE_URL, link["href"])
        parsed = urlsplit(url)

        if (
            parsed.scheme != "https"
            or parsed.hostname != "courses.aber.ac.uk"
        ):
            continue

        parts = parsed.path.strip("/").split("/")

        if (
            len(parts) < 2
            or parts[0] not in ("undergraduate", "postgraduate")
        ):
            continue

        label = clean_text(link)

        match = re.fullmatch(
            rf"(.+)\s+\(({AWARD_PATTERN}),\s*"
            r"(\d+(?:\.\d+)?)\s+years?\)",
            label,
            flags=re.IGNORECASE,
        )

        if match is None:
            continue

        title, award, years = match.groups()

        canonical_url = urlunsplit((
            "https",
            "courses.aber.ac.uk",
            parsed.path,
            "",
            "",
        ))

        record = {
            "catalogue_title": label,
            "subject_title": title.strip(),
            "normalised_title": normalise(title),
            "award": award.upper(),
            "duration_years": years,
            "catalogue_level": parts[0],
            "source_url": canonical_url,
        }

        previous = records.get(canonical_url)

        if previous and previous != record:
            raise ValueError(
                f"Conflicting catalogue labels for {canonical_url}"
            )

        records[canonical_url] = record

    if not records:
        raise ValueError(
            "No course records extracted; catalogue structure needs review."
        )

    return list(records.values())


class Command(BaseCommand):
    help = (
        "Preview Aberystwyth course-identity candidates. "
        "Does not approve mappings or change database records."
    )

    def handle(self, *args, **options):
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )

        try:
            with requests.Session() as session:
                session.headers["User-Agent"] = (
                    "TuitionCompareSourceInspector/1.0"
                )

                session.mount(
                    "https://",
                    HTTPAdapter(max_retries=retry),
                )

                self.stdout.write(
                    f"Reading: {CATALOGUE_URL}"
                )

                response = session.get(
                    CATALOGUE_URL,
                    timeout=(30, 60),
                )
                response.raise_for_status()

            if (
                response.url.rstrip("/")
                != CATALOGUE_URL.rstrip("/")
            ):
                raise ValueError(
                    f"Unexpected redirect: {response.url}"
                )

            html = response.content.decode("utf-8-sig")
            catalogue = parse_catalogue(html)

        except (
            requests.RequestException,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            raise CommandError(str(exc)) from exc

        courses = Course.objects.filter(
            university_id="UK002",
            is_active=True,
        ).order_by("course_id")

        results = []

        for course in courses:
            identity = stored_identity(course)

            level = (
                "undergraduate"
                if course.level in ("UG", "TOPUP")
                else "postgraduate"
            )

            candidates = []

            for source in catalogue:
                if source["catalogue_level"] != level:
                    continue

                if (
                    source["normalised_title"]
                    != identity["normalised_title"]
                ):
                    continue

                flags = []

                if identity["award"] != source["award"]:
                    flags.append("award_mismatch")

                if (
                    duration_years(course.duration)
                    != source["duration_years"]
                ):
                    flags.append("duration_mismatch")

                flags.append("course_page_and_intake_not_yet_verified")

                candidates.append({
                    **source,
                    "flags": flags,
                    "approved": False,
                })

            results.append({
                "course_id": course.course_id,
                "title": course.title,
                "level": course.level,
                "duration": course.duration,
                "start_month": course.start_month,
                "start_year": course.start_year,
                "stored_url": course.course_url,
                "candidates": candidates,
                "status": (
                    "needs_identity_review"
                    if candidates
                    else "no_catalogue_title_candidate"
                ),
                "approved": False,
            })

        generated_at = datetime.now(timezone.utc)
        stamp = generated_at.strftime("%Y%m%dT%H%M%S%fZ")

        directory = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aberystwyth"
            / stamp
        )
        directory.mkdir(parents=True, exist_ok=True)

        snapshot = directory / "catalogue.html"
        snapshot.write_bytes(response.content)

        report = {
            "generated_at": generated_at.isoformat(),
            "university_id": "UK002",
            "database_changes": False,
            "catalogue_url": response.url,
            "catalogue_snapshot": str(snapshot),
            "catalogue_sha256": hashlib.sha256(
                response.content
            ).hexdigest(),
            "catalogue_records": len(catalogue),
            "database_courses": len(results),
            "courses_with_candidates": sum(
                bool(row["candidates"])
                for row in results
            ),
            "courses_without_candidates": sum(
                not row["candidates"]
                for row in results
            ),
            "records": results,
        }

        output = directory / "course-matches.json"

        output.write_text(
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            f"Catalogue records: {report['catalogue_records']}"
        )
        self.stdout.write(
            f"Database courses: {report['database_courses']}"
        )
        self.stdout.write(
            "Courses with candidates: "
            f"{report['courses_with_candidates']}"
        )
        self.stdout.write(
            "Courses without candidates: "
            f"{report['courses_without_candidates']}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Candidates only. No mappings approved. "
            "No database records changed."
        )