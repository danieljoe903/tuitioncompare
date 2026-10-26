import hashlib
import json
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import DepositPolicy, University


ACADEMIC_YEAR = "2026/27"

SOURCE_URL = (
    "https://www.abertay.ac.uk/study-apply/"
    "money-fees-and-funding/tuition-fees-2026-2027/"
)

SECTION_HEADINGS = (
    "Undergraduate Tuition Fees - International Students 2026/27",
    "Postgraduate Tuition Fees - International Students 2026/27",
)

# Reviewed country identities, not default deposit amounts.
COUNTRY_CODES = {
    "afghanistan": "AF",
    "iran": "IR",
    "pakistan": "PK",
    "bangladesh": "BD",
}

REVIEWED_INDIAN_STATES = {
    "punjab",
    "haryana",
    "gujarat",
    "telangana",
}


def clean_text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def extract_deposit_section(soup, heading_text):
    headings = soup.find_all(
        lambda tag: (
            tag.name in ("h2", "h3", "h4")
            and clean_text(tag) == heading_text
        )
    )

    if len(headings) != 1:
        raise ValueError(
            f"Expected one section: {heading_text!r}; "
            f"found {len(headings)}."
        )

    paragraphs = []
    list_items = []
    stopped = False

    for element in headings[0].next_elements:
        name = getattr(element, "name", None)

        if name in ("h1", "h2", "h3", "h4"):
            label = clean_text(element).casefold()

            if label.startswith("early bird"):
                stopped = True
                break

            if (
                label.startswith("undergraduate tuition fees")
                or label.startswith("postgraduate tuition fees")
                or label.startswith("self-funding")
            ):
                raise ValueError(
                    "Deposit section ended unexpectedly."
                )

        if name == "p":
            paragraphs.append(clean_text(element))

        elif name == "li":
            list_items.append(clean_text(element))

    if not stopped:
        raise ValueError(
            "Could not identify the end of the deposit section."
        )

    if not paragraphs or not list_items:
        raise ValueError("Deposit evidence is incomplete.")

    return paragraphs, list_items


def parse_rules(paragraphs, list_items):
    text = " ".join(paragraphs)

    # Extract the percentage from the published sentence.
    standard = re.findall(
        r"deposit equal to\s+(\d+(?:\.\d+)?)%"
        r"\s+of your net tuition fee",
        text,
        flags=re.IGNORECASE,
    )

    full_payment = re.findall(
        r"required to pay\s+(\d+(?:\.\d+)?)%"
        r"\s+deposit before you can apply for your CAS"
        r"\s+if you legally reside in",
        text,
        flags=re.IGNORECASE,
    )

    nigeria = re.findall(
        r"Applicants who legally reside in Nigeria"
        r"\s+need to pay a flat deposit of"
        r"\s+£\s*([\d,]+(?:\.\d{2})?)",
        text,
        flags=re.IGNORECASE,
    )
    # Repeated identical values are acceptable.
    # Different values still fail the ambiguity check below.
    standard = list(dict.fromkeys(standard))
    full_payment = list(dict.fromkeys(full_payment))

    nigeria = list(dict.fromkeys(
        value.replace(",", "")
        for value in nigeria
    ))
    if not (
        len(standard) == 1
        and len(full_payment) == 1
        and len(nigeria) == 1
    ):
        raise ValueError(
            "Deposit extraction needs review: "
            f"standard percentage matches={standard!r}; "
            f"full-payment matches={full_payment!r}; "
            f"Nigeria amount matches={nigeria!r}."
        )

    standard_percent = standard[0]
    full_percent = full_payment[0]
    nigeria_amount = nigeria[0].replace(",", "")

    # Changes to the reviewed policy require inspection.
    if (
        standard_percent != "50"
        or full_percent != "100"
        or nigeria_amount not in ("4500", "4500.00")
    ):
        raise ValueError(
            "Published deposit amounts changed. "
            "Review the source before importing."
        )

    if not re.search(
        r"net tuition fee is calculated after deducting"
        r"\s+any University scholarships from the gross fee",
        text,
        flags=re.IGNORECASE,
    ):
        raise ValueError("The net-tuition basis was not confirmed.")

    if "risk factors" not in text.casefold():
        raise ValueError(
            "Individual deposit assessment wording needs review."
        )

    countries = set()
    states = set()

    for item in list_items:
        label = item.strip().rstrip(".").casefold()

        if label in COUNTRY_CODES:
            countries.add(COUNTRY_CODES[label])
            continue

        if label.startswith("the indian states of "):
            state_text = label.removeprefix(
                "the indian states of "
            )

            states.update(
                part.strip()
                for part in re.split(
                    r",\s*(?:and\s+)?|\s+and\s+",
                    state_text,
                )
                if part.strip()
            )

            continue

        # The September/January instalment bullet is not an exception.
        if re.match(r"a 50% deposit is due\b", label):
            continue

        raise ValueError(
            f"Unrecognised deposit list item: {item!r}"
        )

    if countries != set(COUNTRY_CODES.values()):
        raise ValueError(
            "Country exceptions changed; manual review required."
        )

    if states != REVIEWED_INDIAN_STATES:
        raise ValueError(
            "Indian state exceptions changed; manual review required."
        )

    return {
        "schema_version": 1,
        "fee_status": "INTL",
        "payment_stage": "before_cas",
        "self_funded": True,
        "requires_student_visa": True,
        "basis": "net_tuition",
        "standard_percentage": standard_percent,
        "country_fixed_amounts": {
            "NG": f"{float(nigeria_amount):.2f}",
        },
        "full_payment_percentage": full_percent,
        "full_payment_countries": sorted(countries),
        "full_payment_regions": {
            "IN": sorted(states),
        },
        "individual_override_possible": True,
        "early_payment_discounts_included": False,
    }


def snapshot(policy):
    if policy is None:
        return None

    return {
        "academic_year": policy.academic_year,
        "currency": policy.currency,
        "rules": policy.rules,
        "source_url": policy.source_url,
        "source_sha256": policy.source_sha256,
        "verified": policy.verified,
    }


class Command(BaseCommand):
    help = (
        "Scrape and preview/save Abertay's reviewed "
        "2026/27 international deposit policy."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            university = University.objects.get(
                pk="UK001",
                country="UK",
            )
        except University.DoesNotExist as exc:
            raise CommandError(
                "Abertay university UK001 was not found."
            ) from exc

        if university.name.casefold() != "abertay university":
            raise CommandError(
                "UK001 does not identify Abertay University."
            )

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        try:
            with requests.Session() as session:
                session.headers["User-Agent"] = (
                    "TuitionCompareDepositInspector/1.0"
                )

                session.mount(
                    "https://",
                    HTTPAdapter(max_retries=retry),
                )

                response = session.get(
                    SOURCE_URL,
                    timeout=(30, 60),
                )
                response.raise_for_status()

            if response.url.rstrip("/") != SOURCE_URL.rstrip("/"):
                raise ValueError(
                    f"Unexpected redirect: {response.url}"
                )
            # Decode explicitly so £ and other characters remain intact.
            try:
                html = response.content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    "The source page encoding changed; review required."
                ) from exc

            soup = BeautifulSoup(html, "html.parser")
            
            heading = soup.find("h1")

            if (
                heading is None
                or clean_text(heading) != "Tuition Fees (2026-2027)"
            ):
                raise ValueError(
                    "Unexpected fee-page title or academic year."
                )

            extracted_rules = []
            evidence = []

            for section_heading in SECTION_HEADINGS:
                paragraphs, list_items = extract_deposit_section(
                    soup,
                    section_heading,
                )

                extracted_rules.append(
                    parse_rules(paragraphs, list_items)
                )

                evidence.append({
                    "heading": section_heading,
                    "paragraphs": paragraphs,
                    "list_items": list_items,
                })

            if extracted_rules[0] != extracted_rules[1]:
                raise ValueError(
                    "UG and PG deposit rules differ; review required."
                )

        except (requests.RequestException, ValueError) as exc:
            raise CommandError(
                f"Deposit import stopped: {exc}. "
                "No database records changed."
            ) from exc

        source_text = json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )

        digest = hashlib.sha256(
            source_text.encode("utf-8")
        ).hexdigest()

        defaults = {
            "currency": "GBP",
            "rules": extracted_rules[0],
            "source_url": SOURCE_URL,
            "source_text": source_text,
            "source_sha256": digest,
            "verified": True,
        }

        checked_at = timezone.now()

        log_directory = (
            Path(settings.BASE_DIR)
            / "logs"
            / "abertay-deposits"
        )
        log_directory.mkdir(parents=True, exist_ok=True)

        report_path = log_directory / (
            f"deposit-{checked_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        )

        # Lock the university row to serialize this policy update.
        with transaction.atomic():
            University.objects.select_for_update().get(
                pk=university.pk
            )

            policy = DepositPolicy.objects.filter(
                university=university,
                academic_year=ACADEMIC_YEAR,
            ).first()

            before = snapshot(policy)

            after = {
                "academic_year": ACADEMIC_YEAR,
                "currency": "GBP",
                "rules": defaults["rules"],
                "source_url": SOURCE_URL,
                "source_sha256": digest,
                "verified": True,
            }

            changed = before != after

            report = {
                "generated_at": checked_at.isoformat(),
                "mode": "apply" if options["apply"] else "preview",
                "university_id": university.pk,
                "changed": changed,
                "before": before,
                "after": after,
                "source_evidence": evidence,
            }

            # Save the review evidence before database writes.
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            if options["apply"]:
                DepositPolicy.objects.update_or_create(
                    university=university,
                    academic_year=ACADEMIC_YEAR,
                    defaults={
                        **defaults,
                        "checked_at": checked_at,
                    },
                )

        self.stdout.write(
            json.dumps(after, indent=2, ensure_ascii=False)
        )
        self.stdout.write(f"\nPolicy changed: {changed}")
        self.stdout.write(f"Report: {report_path}")

        if options["apply"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "Deposit policy saved to the database. "
                    "Tuition prices and scholarships unchanged."
                )
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )