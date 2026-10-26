import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.models import Scholarship


SCHOLARSHIP_URL = (
    "https://www.abertay.ac.uk/study-apply/"
    "money-fees-and-funding/scholarships/"
    "abertay-international-scholarship/"
)

TERMS_URL = (
    "https://www.abertay.ac.uk/study-apply/"
    "money-fees-and-funding/terms-conditions/"
)


def clean_text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def extract_section(soup, heading_text):
    """Read text after one heading, stopping at the next peer heading."""
    headings = soup.find_all(
        lambda tag: tag.name in ("h2", "h3", "h4")
        and clean_text(tag).casefold() == heading_text.casefold()
    )

    if len(headings) != 1:
        raise ValueError(
            f"Expected one {heading_text!r} heading; "
            f"found {len(headings)}."
        )

    heading = headings[0]
    level = int(heading.name[1])
    paragraphs = []

    for element in heading.next_elements:
        name = getattr(element, "name", None)

        if name in ("h1", "h2", "h3", "h4"):
            if int(name[1]) <= level:
                break

        if name in ("p", "li"):
            # Avoid repeating paragraphs nested inside list items.
            if name == "p" and element.find_parent("li"):
                continue

            value = clean_text(element)
            if value:
                paragraphs.append(value)

    return paragraphs


class Command(BaseCommand):
    help = (
        "Extract Abertay scholarship evidence and compare existing "
        "scholarship records. No database writes."
    )

    def download(self, session, url):
        response = session.get(url, timeout=(30, 60))
        response.raise_for_status()

        if response.url != url:
            raise ValueError(
                f"Unexpected redirect: {response.url}"
            )

        return BeautifulSoup(response.content, "html.parser")

    def handle(self, *args, **options):
        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        try:
            with requests.Session() as session:
                session.headers.update({
                    "User-Agent": "TuitionCompareScholarshipImporter/1.0"
                })
                session.mount(
                    "https://",
                    HTTPAdapter(max_retries=retries),
                )

                scholarship_soup = self.download(
                    session,
                    SCHOLARSHIP_URL,
                )
                terms_soup = self.download(session, TERMS_URL)

            title = scholarship_soup.find("h1")

            if (
                title is None
                or clean_text(title) != "Abertay International Scholarship"
            ):
                raise ValueError("Unexpected scholarship page.")

            overview = extract_section(
                scholarship_soup,
                "Overview",
            )
            eligibility = extract_section(
                scholarship_soup,
                "Eligibility",
            )
            application = extract_section(
                scholarship_soup,
                "How to Apply",
            )

            overview_text = " ".join(overview)

            amounts = {
                match.replace(",", "")
                for match in re.findall(
                    r"up to\s*£([\d,]+(?:\.\d{2})?)\s*per year",
                    overview_text,
                    flags=re.IGNORECASE,
                )
            }

            if len(amounts) != 1:
                raise ValueError(
                    "Annual maximum award is missing or ambiguous."
                )

            if not eligibility or not application:
                raise ValueError(
                    "Scholarship conditions were not extracted."
                )

            annual_maximum = next(iter(amounts))

            terms = {
                name: extract_section(terms_soup, name)
                for name in (
                    "Eligibility",
                    "Acceptance",
                    "Payment",
                    "Multiple Awards",
                    "Students from Partner institutions",
                )
            }

        except (requests.RequestException, ValueError) as exc:
            raise CommandError(
                f"Scholarship extraction failed: {exc}"
            ) from exc

        existing = list(
            Scholarship.objects.filter(
                university__name__iexact="Abertay University",
                name__iexact="Abertay International Scholarship",
            ).values(
                "id",
                "name",
                "level",
                "award_value",
                "currency",
                "deadline",
                "eligibility",
                "requirements",
                "url",
            )
        )

        for row in existing:
            row["deadline"] = (
                row["deadline"].isoformat()
                if row["deadline"] else None
            )

        report = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "database_changes": False,
            "scholarship_name": clean_text(title),
            "source_url": SCHOLARSHIP_URL,
            "terms_source_url": TERMS_URL,
            "currency": "GBP",
            "amount_type": "annual_maximum",
            "annual_maximum": annual_maximum,
            "overview": overview,
            "eligibility": eligibility,
            "application": application,
            "terms": terms,
            "existing_database_records": existing,
            "calculation_ready": False,
            "review_reason": (
                "Maximum award is not a guaranteed deduction. "
                "Verify award period, conditions and combination rules "
                "before updating calculation rules."
            ),
        }

        output = (
            settings.BASE_DIR
            / "abertay_scholarships_preview.json"
        )
        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        self.stdout.write(
            f"Scholarship: {report['scholarship_name']}"
        )
        self.stdout.write(
            f"Annual maximum: GBP {annual_maximum}"
        )
        self.stdout.write(
            f"Existing scholarship records: {len(existing)}"
        )
        self.stdout.write(f"Preview saved to: {output}")
        self.stdout.write("No database records changed.")