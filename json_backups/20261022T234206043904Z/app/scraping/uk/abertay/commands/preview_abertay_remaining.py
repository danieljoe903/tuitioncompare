import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.models import Course
from extract_abertay_course import (
    MissingFeeTable,
    parse_course_html,
)


BASE_URL = "https://www.abertay.ac.uk"
YEAR = "2026/27"

CATALOGUES = {
    "postgraduate_taught": (
        "Postgraduate Taught",
        "/course-search/postgraduate-taught/",
    ),
    "postgraduate_research": (
        "Postgraduate Research",
        "/course-search/postgraduate-research/",
    ),
}


def clean_text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def normalise_title(title):
    """Find candidates only; never approve a match."""
    title = title.casefold().replace("&", " and ")
    title = re.sub(r"\bbachelor of laws\b", "llb", title)
    title = re.sub(r"\bhonours\b", "hons", title)
    title = re.sub(r"\bux\b", "user experience", title)
    title = re.sub(
        r"\b(bsc|ba|beng|meng|llb|msc|ma|mres|mprof|hons)\b",
        " ",
        title,
    )
    return " ".join(re.findall(r"[a-z0-9]+", title))


def valid_url(url, prefix):
    parsed = urlsplit(url)

    return (
        parsed.scheme == "https"
        and parsed.netloc == "www.abertay.ac.uk"
        and parsed.path.startswith(prefix)
        and parsed.path.rstrip("/") != prefix.rstrip("/")
    )


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")

    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def download(session, url):
    response = session.get(url, timeout=(30, 60))
    response.raise_for_status()

    parsed = urlsplit(response.url)

    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.abertay.ac.uk"
    ):
        raise ValueError("Unexpected redirect outside Abertay.")

    time.sleep(1)
    return response


def discover(session, study_level, prefix):
    url = BASE_URL + "/course-search/?" + urlencode({
        "studyLevel": study_level,
    })

    courses = {}
    seen_pages = set()
    seen_urls = set()

    for _ in range(30):
        if url in seen_urls:
            raise ValueError("Catalogue pagination repeated a URL.")

        seen_urls.add(url)
        response = download(session, url)

        if (
            urlsplit(response.url).path.rstrip("/")
            != "/course-search"
        ):
            raise ValueError(
                "Catalogue redirected to an unexpected page."
            )

        soup = BeautifulSoup(response.text, "html.parser")
        page_courses = {}

        for link in soup.select("a[href]"):
            target = urljoin(response.url, link["href"])
            parsed = urlsplit(target)

            if (
                parsed.hostname == "search.abertay.ac.uk"
                and parsed.path == "/s/redirect"
            ):
                destinations = parse_qs(
                    parsed.query
                ).get("url", [])

                if len(destinations) != 1:
                    continue

                target = destinations[0]

            if not valid_url(target, prefix):
                continue

            title = clean_text(link)

            if not title or title.casefold() == "view course":
                continue

            parsed = urlsplit(target)
            target = parsed._replace(
                query="", fragment=""
            ).geturl()

            page_courses.setdefault(target, title)

        if not page_courses:
            raise ValueError(
                f"No course links found for {study_level}."
            )

        fingerprint = tuple(sorted(page_courses))

        if fingerprint in seen_pages:
            raise ValueError(
                "Catalogue repeated its course results."
            )

        seen_pages.add(fingerprint)
        courses.update(page_courses)

        next_url = None

        for link in soup.select("a[href]"):
            if clean_text(link).casefold() != "next":
                continue

            target = urljoin(response.url, link["href"])
            parsed = urlsplit(target)
            query = parse_qs(parsed.query)

            if (
                parsed.scheme == "https"
                and parsed.netloc == "www.abertay.ac.uk"
                and parsed.path.rstrip("/") == "/course-search"
                and query.get("studyLevel") == [study_level]
                and "pageNumber" in query
            ):
                next_url = target
                break

        if next_url is None:
            return courses

        url = next_url

    raise ValueError("Catalogue exceeded the pagination limit.")


class Command(BaseCommand):
    help = (
        "Collect Abertay postgraduate evidence and remaining "
        "undergraduate candidates. No database writes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            help="Optional output JSON path.",
        )

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)

        output = (
            Path(options["output"])
            if options.get("output")
            else root / "abertay_remaining_preview.json"
        )

        try:
            mapping = json.loads(
                (root / "abertay_course_mapping.json")
                .read_text(encoding="utf-8-sig")
            )
            undergraduate = json.loads(
                (root / "abertay_courses_preview.json")
                .read_text(encoding="utf-8-sig")
            )

            if undergraduate.get("academic_year") != YEAR:
                raise ValueError(
                    "Unexpected undergraduate academic year."
                )

            if not isinstance(mapping.get("mappings"), list):
                raise ValueError("Invalid mapping file.")

            ug_records = undergraduate.get("records")

            if (
                not isinstance(ug_records, list)
                or not ug_records
                or any(
                    not isinstance(row, dict)
                    or row.get("status") != "extracted"
                    or not row.get("source_url")
                    or not isinstance(row.get("data"), dict)
                    for row in ug_records
                )
            ):
                raise ValueError(
                    "Undergraduate extraction is incomplete."
                )

        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise CommandError(
                f"Cannot load input files: {exc}"
            ) from exc

        courses = list(
            Course.objects.filter(
                university__name__iexact="Abertay University",
                university__country="UK",
                is_active=True,
            ).prefetch_related("tuitions")
        )

        def candidate_records(title, levels):
            key = normalise_title(title)

            if not key:
                return []

            return [
                {
                    "course_id": course.course_id,
                    "title": course.title,
                    "duration": course.duration,
                    "level": course.level,
                    "start_year": course.start_year,
                    "start_month": course.start_month,
                    "course_url": course.course_url,
                }
                for course in courses
                if course.level in levels
                and normalise_title(course.title) == key
            ]

        mapped_urls = {
            entry["source_url"]
            for entry in mapping["mappings"]
            if entry.get("approved") is True
        }

        remaining = []

        for record in ug_records:
            if record["source_url"] in mapped_urls:
                continue

            data = record["data"]
            candidates = candidate_records(
                data.get("title", ""),
                {"UG", "TOPUP"},
            )

            remaining.append({
                "source_url": record["source_url"],
                "catalogue_title": record.get("catalogue_title"),
                "data": data,
                "candidates": candidates,
                "status": (
                    "identity_review_required"
                    if candidates
                    else "no_normalised_title_match"
                ),
                "approved": False,
            })

        eu_records = [
            {
                "course_id": course.course_id,
                "course_title": course.title,
                "tuition_id": tuition.pk,
                "stored_fee": str(tuition.tuition_fee),
                "currency": tuition.currency,
                "status": "outside_current_update_scope",
            }
            for course in courses
            for tuition in course.tuitions.all()
            if tuition.residency == "EU"
        ]

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "academic_year": YEAR,
            "database_changes": False,
            "collection_complete": False,
            "undergraduate_remaining": remaining,
            "eu_records": eu_records,
            "eu_mapping": {
                "status": "outside_current_update_scope",
                "note": (
                    "Only assessed international fees are currently "
                    "being updated. EU database rows are unchanged."
                ),
            },
            "postgraduate": [],
            "catalogue_counts": {},
            "catalogue_errors": [],
        }

        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        with requests.Session() as session:
            session.headers["User-Agent"] = (
                "TuitionCompareSourceInspector/1.0"
            )
            session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )

            for kind, (study_level, prefix) in CATALOGUES.items():
                self.stdout.write(
                    f"\nDiscovering {study_level}..."
                )
                self.stdout.flush()

                try:
                    discovered = discover(
                        session, study_level, prefix
                    )
                except (
                    requests.RequestException,
                    ValueError,
                ) as exc:
                    report["catalogue_errors"].append({
                        "catalogue": kind,
                        "error": str(exc),
                    })
                    self.stderr.write(str(exc))
                    continue

                report["catalogue_counts"][kind] = len(discovered)

                self.stdout.write(
                    f"Discovered: {len(discovered)} pages"
                )

                for url, catalogue_title in discovered.items():
                    record = {
                        "kind": kind,
                        "source_url": url,
                        "catalogue_title": catalogue_title,
                        "approved": False,
                    }

                    self.stdout.write(f"Reading: {url}")
                    self.stdout.flush()

                    try:
                        response = download(session, url)

                        if not valid_url(response.url, prefix):
                            raise ValueError(
                                "Redirected outside this course category."
                            )

                        record["response_url"] = response.url
                        soup = BeautifulSoup(
                            response.text, "html.parser"
                        )
                        heading = soup.find("h1")

                        if heading is None:
                            raise ValueError("Missing course title.")

                        source_title = clean_text(heading)

                        if not source_title:
                            raise ValueError("Empty course title.")

                        record["source_title"] = source_title

                        details = {}

                        for item in soup.select("div.e-item"):
                            label = item.find("h4")
                            value = item.find(
                                "span", recursive=False
                            )

                            if label is not None and value is not None:
                                details[clean_text(label)] = (
                                    clean_text(value)
                                )

                        record["course_details"] = details

                        levels = (
                            {"PG", "PGT"}
                            if kind == "postgraduate_taught"
                            else {"PG", "PGT", "MRES"}
                        )

                        record["candidates"] = candidate_records(
                            source_title, levels
                        )

                        fees_heading = soup.find(
                            lambda tag: tag.name in ("h2", "h3")
                            and clean_text(tag).casefold()
                            == "fees and funding"
                        )

                        if fees_heading is not None:
                            fee_section = fees_heading.find_parent(
                                "div", class_="container"
                            )

                            if fee_section is None:
                                fee_section = fees_heading.parent

                            record["fee_context"] = clean_text(
                                fee_section
                            )

                        data = parse_course_html(
                            response.text, YEAR
                        )
                        data["fee_source_url"] = response.url
                        data["fee_method"] = "course_page"

                        record["data"] = data
                        record["status"] = "extracted"

                        self.stdout.write(
                            f"Extracted: {data['title']} — "
                            f"{data['currency']} "
                            f"{data['annual_tuition']}"
                        )

                    except requests.RequestException as exc:
                        record["status"] = "request_failed"
                        record["error_code"] = "request_failed"
                        record["error"] = str(exc)
                        self.stderr.write(str(exc))

                    except MissingFeeTable as exc:
                        record["status"] = "needs_review"
                        record["error_code"] = "missing_fee_table"
                        record["error"] = str(exc)

                        self.stdout.write(
                            f"Central fee evidence required: {exc}"
                        )

                    except ValueError as exc:
                        record["status"] = "needs_review"
                        record["error_code"] = "parse_failed"
                        record["error"] = str(exc)
                        self.stdout.write(
                            f"Needs review: {exc}"
                        )

                    report["postgraduate"].append(record)

        fatal_records = [
            record
            for record in report["postgraduate"]
            if (
                record.get("status") != "extracted"
                and not (
                    record.get("status") == "needs_review"
                    and record.get("error_code")
                    == "missing_fee_table"
                    and record.get("source_title")
                )
            )
        ]

        incomplete = bool(
            report["catalogue_errors"]
            or fatal_records
            or set(report["catalogue_counts"]) != set(CATALOGUES)
        )

        report["collection_complete"] = not incomplete

        if incomplete:
            failed_output = output.with_name(
                output.stem + ".failed.json"
            )
            write_json(failed_output, report)

            raise CommandError(
                "Postgraduate collection was incomplete. "
                f"Diagnostic report: {failed_output}. "
                "The previous successful output was preserved."
            )

        write_json(output, report)

        extracted = sum(
            row["status"] == "extracted"
            for row in report["postgraduate"]
        )
        central_required = sum(
            row.get("error_code") == "missing_fee_table"
            for row in report["postgraduate"]
        )

        self.stdout.write(
            f"\nRemaining undergraduate pages: {len(remaining)}"
        )
        self.stdout.write(
            f"Postgraduate pages processed: "
            f"{len(report['postgraduate'])}"
        )
        self.stdout.write(
            f"Postgraduate fees extracted: {extracted}"
        )
        self.stdout.write(
            f"Pages requiring central fee evidence: "
            f"{central_required}"
        )
        self.stdout.write(f"Saved to: {output}")
        self.stdout.write("No database records changed.")