import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.core.management.base import BaseCommand, CommandError

from app.models import Course, Scholarship, Tuition, University


SOURCES = {
    "international_fees": (
        "https://www.aber.ac.uk/en/study-with-us/"
        "international/fees-scholarships/"
    ),
    "scholarship_index": (
        "https://www.aber.ac.uk/en/study-with-us/"
        "international/scholarships/"
    ),
    "global_citizens_scholarship": (
        "https://www.aber.ac.uk/en/study-with-us/"
        "international/scholarships/global-citizens-scholarship/"
    ),
    "deposits": (
        "https://www.aber.ac.uk/en/study-with-us/"
        "international/cas-and-deposit-information/"
    ),
}


def clean_text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def extract_page(html, response_url):
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")

    if heading is None or not clean_text(heading):
        raise ValueError("The source page has no course/policy title.")

    title = clean_text(heading)

    # Prefer the page's main content over navigation and footer text.
    main = soup.find("main") or soup.find(
        attrs={"role": "main"}
    ) or soup.body

    if main is None:
        raise ValueError("The source page has no readable content.")

    for element in main.find_all(
        ["script", "style", "nav", "footer", "noscript"]
    ):
        element.decompose()

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
            tables.append({
                "rows": rows,
                "links": [
                    {
                        "text": clean_text(link),
                        "url": urljoin(response_url, link["href"]),
                    }
                    for link in table.find_all("a", href=True)
                ],
            })

    links = []
    seen = set()

    for link in main.find_all("a", href=True):
        url = urljoin(response_url, link["href"])

        if urlsplit(url).scheme not in ("https", "http"):
            continue

        label = clean_text(link)
        key = (label, url)

        if key in seen:
            continue

        seen.add(key)
        links.append({"text": label, "url": url})

    return {
        "title": title,
        "headings": [
            {
                "level": element.name,
                "text": clean_text(element),
            }
            for element in main.find_all(["h1", "h2", "h3", "h4"])
            if clean_text(element)
        ],
        "text": clean_text(main),
        "tables": tables,
        "links": links,
    }


class Command(BaseCommand):
    help = (
        "Collect Aberystwyth official tuition, scholarship and "
        "deposit evidence. Read-only database inspection."
    )

    def handle(self, *args, **options):
        try:
            university = University.objects.get(
                pk="UK002",
                country="UK",
            )
        except University.DoesNotExist as exc:
            raise CommandError(
                "Aberystwyth University UK002 was not found."
            ) from exc

        if university.name.casefold() != "aberystwyth university":
            raise CommandError(
                "UK002 does not identify Aberystwyth University."
            )

        generated_at = datetime.now(timezone.utc)
        stamp = generated_at.strftime("%Y%m%dT%H%M%S%fZ")

        directory = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aberystwyth"
            / stamp
        )
        directory.mkdir(parents=True, exist_ok=True)

        report = {
            "generated_at": generated_at.isoformat(),
            "university_id": university.pk,
            "database_changes": False,
            "sources": {},
            "courses": list(
                Course.objects.filter(
                    university=university,
                ).order_by("course_id").values()
            ),
            "tuitions": list(
                Tuition.objects.filter(
                    course__university=university,
                ).values()
            ),
            "scholarships": list(
                Scholarship.objects.filter(
                    university=university,
                ).values()
            ),
            "review_notes": [
                "Do not infer fee bands from award abbreviations.",
                "Match academic year and intake before assigning fees.",
                "Verify scholarship identity and source university.",
                "Accommodation benefits are not tuition deductions.",
                "An offer-specific deposit is not a published fixed amount.",
                "This collection does not approve course mappings.",
            ],
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

        failures = []

        with requests.Session() as session:
            session.headers["User-Agent"] = (
                "TuitionCompareSourceInspector/1.0"
            )

            session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )

            for name, url in SOURCES.items():
                self.stdout.write(f"Reading: {url}")

                record = {
                    "requested_url": url,
                    "status": "pending",
                }

                try:
                    response = session.get(
                        url,
                        timeout=(30, 60),
                    )
                    response.raise_for_status()

                    parsed = urlsplit(response.url)

                    if (
                        parsed.scheme != "https"
                        or parsed.hostname not in (
                            "www.aber.ac.uk",
                            "aber.ac.uk",
                        )
                    ):
                        raise ValueError(
                            "Redirected outside the official website."
                        )

                    record["response_url"] = response.url

                    html = response.content.decode("utf-8-sig")
                    snapshot = directory / f"{name}.html"
                    snapshot.write_bytes(response.content)

                    record.update(
                        extract_page(html, response.url)
                    )

                    record.update({
                        "status": "collected",
                        "html_snapshot": str(snapshot),
                        "sha256": hashlib.sha256(
                            response.content
                        ).hexdigest(),
                    })

                    self.stdout.write(
                        f"Collected: {record['title']} "
                        f"— {len(record['tables'])} tables"
                    )

                except (
                    requests.RequestException,
                    UnicodeDecodeError,
                    ValueError,
                ) as exc:
                    record["status"] = "needs_review"
                    record["error"] = str(exc)
                    failures.append(name)

                    self.stderr.write(
                        f"Needs review: {name}: {exc}"
                    )

                report["sources"][name] = record
                time.sleep(1)

        report["collection_complete"] = not failures

        output = directory / "source-review.json"

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
            f"\nDatabase courses: {len(report['courses'])}"
        )
        self.stdout.write(
            f"Tuition records: {len(report['tuitions'])}"
        )
        self.stdout.write(
            f"Scholarships: {len(report['scholarships'])}"
        )
        self.stdout.write(
            f"Sources collected: {len(SOURCES) - len(failures)}"
        )
        self.stdout.write(f"Sources needing review: {len(failures)}")
        self.stdout.write(f"Report: {output}")
        self.stdout.write("No database records changed.")

        if failures:
            raise CommandError(
                "Some sources could not be collected. "
                "The report contains the errors."
            )