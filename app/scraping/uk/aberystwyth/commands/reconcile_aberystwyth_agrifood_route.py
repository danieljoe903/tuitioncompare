import copy
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import (
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)

from app.scraping.uk.aberystwyth.commands.import_aberystwyth_welsh_education_fees import download, text


COURSE_ID = "C_UK002_92"
POSSIBLE_OVERLAP_ID = "C_UK002_65"

ACADEMIC_YEAR = "2026/27"
SOURCE_COURSE_CODE = "D67ND3"

COURSE_URL = (
    "https://www.aber.ac.uk/en/study-schemes/2026-2027/"
    "schemes/scheme/?scheme=AB-D67ND3-MSC-202701"
)

GENERAL_COURSE_URL = (
    "https://courses.aber.ac.uk/postgraduate/agrifood-innovation/"
)

PROGRAMME_URL = (
    "https://ibersdl.org.uk/building-up-qualifications/"
    "msc-agrifood-innovation/"
)

FEE_URL = "https://ibersdl.org.uk/registration/fees"

FIELDS = (
    "title",
    "level",
    "duration",
    "location",
    "start_month",
    "start_year",
    "intake_months",
    "course_url",
)

EXPECTED_PRICES = {
    "taught online modules (20 credits)": Decimal("1055.00"),
    "60 credit dissertation (for msc)": Decimal("3170.00"),
    "msc or mres (180 credits)": Decimal("9500.00"),
}


def money(value):
    match = re.fullmatch(
        r"£\s*([\d,]+(?:\.\d{2})?)",
        value.strip(),
    )

    if match is None:
        raise ValueError(
            f"Invalid published monetary amount: {value!r}"
        )

    return Decimal(
        match.group(1).replace(",", "")
    ).quantize(Decimal("0.01"))


def extract_reviewed_prices(soup):
    """
    Locate the confirmed 2026/27 column by its heading.

    Do not assume that the current-year price is always the
    second or third table cell.
    """
    found = {
        label: []
        for label in EXPECTED_PRICES
    }

    for table in soup.find_all("table"):
        target_column = None

        for row in table.find_all("tr"):
            cells = [
                text(cell)
                for cell in row.find_all(
                    ["th", "td"],
                    recursive=False,
                )
            ]

            if not cells:
                continue

            year_columns = [
                index
                for index, value in enumerate(cells)
                if re.fullmatch(
                    r"(?:sept(?:ember)?\s*)?"
                    r"2026\s*[-/]\s*(?:20)?27",
                    value.strip(),
                    re.I,
                )
            ]

            if year_columns:
                if len(year_columns) != 1:
                    raise ValueError(
                        "Ambiguous 2026/27 fee column."
                    )

                target_column = year_columns[0]
                continue

            label = cells[0].casefold()

            if label not in EXPECTED_PRICES:
                continue

            if target_column is None:
                raise ValueError(
                    "A fee table contains a required price row "
                    "without a confirmed 2026/27 column heading."
                )

            if (
                target_column == 0
                or target_column >= len(cells)
            ):
                raise ValueError(
                    "A fee row does not align with its "
                    "academic-year column headings."
                )

            found[label].append(
                money(cells[target_column])
            )

    for label, expected in EXPECTED_PRICES.items():
        if found[label] != [expected]:
            raise ValueError(
                f"Expected one {label!r} price of "
                f"{expected} for {ACADEMIC_YEAR}; "
                f"found {found[label]}."
            )

    return {
        "online_module_20_credits": str(
            found["taught online modules (20 credits)"][0]
        ),
        "msc_dissertation_60_credits": str(
            found["60 credit dissertation (for msc)"][0]
        ),
        "upfront_programme_quote": str(
            found["msc or mres (180 credits)"][0]
        ),
    }


def verify_sources(session):
    scheme, scheme_evidence = download(
        session,
        COURSE_URL,
    )

    general, general_evidence = download(
        session,
        GENERAL_COURSE_URL,
    )

    programme, programme_evidence = download(
        session,
        PROGRAMME_URL,
    )

    fees, fee_evidence = download(
        session,
        FEE_URL,
    )

    heading = scheme.find("h1")

    if (
        heading is None
        or text(heading).casefold()
        != "msc agrifood innovation [d67nd3]"
    ):
        raise ValueError(
            "The three-year D67ND3 scheme was not found."
        )

    scheme_text = text(scheme)

    for pattern in (
        r"\bAcademic Year\s+2026/2027\b",
        r"\bJanuary\s+2027\s+Start\b",
        r"\bDuration\s+3\s+years\b",
    ):
        if not re.search(
            pattern,
            scheme_text,
            re.I,
        ):
            raise ValueError(
                "Required D67ND3 evidence missing: "
                f"{pattern}"
            )

    last_intake = re.search(
        r"Last intake year is\s+"
        r"(20\d{2})/(?:20)?\d{2}",
        scheme_text,
        re.I,
    )

    if (
        last_intake
        and int(last_intake.group(1)) < 2026
    ):
        raise ValueError(
            "The scheme closed before the reviewed intake."
        )

    # The general page establishes the programme family
    # and distance-learning delivery.
    # Its five-year duration must not override D67ND3.
    general_heading = general.find("h1")

    if (
        general_heading is None
        or text(general_heading) != "Agrifood Innovation"
    ):
        raise ValueError(
            "General Agrifood programme page does not match."
        )

    general_text = text(general)

    if not re.search(
        r"\bDelivery method\s+Distance learning\b",
        general_text,
        re.I,
    ):
        raise ValueError(
            "Distance-learning delivery is not confirmed."
        )

    if not re.search(
        r"three intakes every year\s*"
        r"\(January, May and September\)",
        general_text,
        re.I,
    ):
        raise ValueError(
            "January, May and September intake months "
            "are not confirmed."
        )

    programme_heading = programme.find("h1")

    if (
        programme_heading is None
        or text(programme_heading)
        != "MSc Agrifood Innovation"
    ):
        raise ValueError(
            "IBERS programme page does not match."
        )

    links = {
        link["href"].rstrip("/")
        for link in programme.find_all(
            "a",
            href=True,
        )
    }

    if GENERAL_COURSE_URL.rstrip("/") not in links:
        raise ValueError(
            "IBERS programme does not link to the "
            "reviewed course family."
        )

    fee_text = text(fees).casefold()

    for phrase in (
        "based on 20-credit",
        "paid up-front",
        "overall cost will depend",
    ):
        if phrase not in fee_text:
            raise ValueError(
                "Fee schedule conditions changed: "
                f"{phrase!r}"
            )

    prices = extract_reviewed_prices(fees)

    return {
        "prices": prices,
        "evidence": {
            "course": scheme_evidence,
            "general_programme_family": general_evidence,
            "programme": programme_evidence,
            "fee_schedule": fee_evidence,
        },
    }


def snapshot(course, source):
    return {
        "course": {
            field: getattr(course, field)
            for field in FIELDS
        },
        "source": {
            "academic_year": source.academic_year,
            "source_url": source.source_url,
            "source_course_name": source.source_course_name,
            "source_course_code": source.source_course_code,
            "fee_basis": source.fee_basis,
            "raw_fees": copy.deepcopy(source.raw_fees),
        },
    }


class Command(BaseCommand):
    help = (
        "Reconcile the three-year Agrifood Innovation "
        "distance-learning MSc D67ND3 and its published "
        "2026/27 module/programme fee schedule."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Save reviewed course and source metadata. "
                "Default is preview."
            ),
        )

    def handle(self, *args, **options):
        try:
            with requests.Session() as session:
                session.headers["User-Agent"] = (
                    "TuitionCompareSourceInspector/1.0"
                )

                retry = Retry(
                    total=3,
                    connect=3,
                    read=3,
                    status=3,
                    backoff_factor=1,
                    status_forcelist=[
                        429,
                        500,
                        502,
                        503,
                        504,
                    ],
                    allowed_methods=["GET"],
                    respect_retry_after_header=True,
                )

                session.mount(
                    "https://",
                    HTTPAdapter(max_retries=retry),
                )

                verification = verify_sources(session)

        except (
            requests.RequestException,
            ValueError,
        ) as exc:
            raise CommandError(
                f"Source verification stopped: {exc}. "
                "No database records changed."
            ) from exc

        prices = verification["prices"]
        evidence = verification["evidence"]

        with transaction.atomic():
            if options["apply"]:
                University.objects.select_for_update().get(
                    university_id="UK002"
                )

            courses = Course.objects.filter(
                course_id=COURSE_ID,
                university_id="UK002",
                is_active=True,
            )

            if options["apply"]:
                courses = courses.select_for_update()

            try:
                course = courses.get()
            except Course.DoesNotExist as exc:
                raise CommandError(
                    "Active distance-learning course not found."
                ) from exc

            if (
                course.title
                != "MSc Agrifood Innovation (Distance Learning)"
                or course.level != "PG"
            ):
                raise CommandError(
                    "Stored title or award differs from "
                    "the reviewed route."
                )

            duplicate = Course.objects.filter(
                university_id="UK002",
                is_active=True,
                course_url=COURSE_URL,
            ).exclude(
                course_id=COURSE_ID
            ).exists()

            if duplicate:
                raise CommandError(
                    "Another active record already uses this "
                    "scheme URL. Review the overlap first."
                )

            tuitions = Tuition.objects.filter(
                course=course,
                residency="INTL",
            )

            if options["apply"]:
                tuitions = tuitions.select_for_update()

            tuitions = list(tuitions)

            if (
                len(tuitions) != 1
                or tuitions[0].currency != "GBP"
            ):
                raise CommandError(
                    "A unique GBP international tuition "
                    "is required."
                )

            tuition = tuitions[0]

            sources = TuitionSource.objects.filter(
                tuition=tuition
            )

            if options["apply"]:
                sources = sources.select_for_update()

            try:
                source = sources.get()
            except TuitionSource.DoesNotExist as exc:
                raise CommandError(
                    "Existing review metadata is required."
                ) from exc

            if not isinstance(source.raw_fees, dict):
                raise CommandError(
                    "Source metadata must be a JSON object."
                )

            if source.raw_fees.get("verified") is True:
                raise CommandError(
                    "Expected an unresolved annual-fee record. "
                    "No verified annual fee will be downgraded."
                )

            before = snapshot(course, source)
            after = copy.deepcopy(before)

            after["course"].update({
                "duration": "3 Years",
                "location": "Distance Learning",
                "start_month": "January",
                "start_year": 2027,
                "intake_months": "January, May, September",
                "course_url": COURSE_URL,
            })

            raw = after["source"]["raw_fees"]

            # The stored numeric tuition is retained, but remains
            # unverified and must not be used as a confirmed annual fee.
            raw.pop("annual_tuition", None)
            raw["fee_categories"] = []

            raw.update({
                "verified": False,
                "fee_verification_status": (
                    "annual_fee_unavailable_module_based"
                ),
                "academic_year": ACADEMIC_YEAR,
                "source_fee_category": "International",
                "currency": "GBP",
                "course_kind": "postgraduate_taught",
                "study_mode": "distance_learning",
                "course_source_url": COURSE_URL,
                "course_identity": {
                    "status": "confirmed_current_catalogue_match",
                    "official_title": "Agrifood Innovation",
                    "official_code": SOURCE_COURSE_CODE,
                    "official_award": "MSC",
                    "source_url": COURSE_URL,
                    "note": (
                        "Three-year distance-learning MSc scheme "
                        "D67ND3. Distinct from the five-year "
                        "D67ND scheme."
                    ),
                },
                "duration_review": {
                    "status": "corrected",
                    "duration": "3 Years",
                    "source_url": COURSE_URL,
                    "reason": (
                        "Official D67ND3 scheme specifies "
                        "three years."
                    ),
                },
                "published_fee_schedule": {
                    "source_verified": True,
                    "academic_year": ACADEMIC_YEAR,
                    "currency": "GBP",
                    "source_url": FEE_URL,
                    "online_module_20_credits": (
                        prices["online_module_20_credits"]
                    ),
                    "msc_dissertation_60_credits": (
                        prices["msc_dissertation_60_credits"]
                    ),
                    "upfront_programme_quote": (
                        prices["upfront_programme_quote"]
                    ),
                    "programme_credits": 180,
                    "quote_conditions": (
                        "Published upfront full-course quote "
                        "based on 20-credit online taught modules. "
                        "The actual pay-per-module total depends "
                        "on module choices and the years in which "
                        "modules are taken."
                    ),
                    "annual_tuition": None,
                    "is_annual_fee": False,
                    "note": (
                        "Use the published programme quote as "
                        "stated; do not derive an annual fee "
                        "by dividing it by the course duration."
                    ),
                },
                "fee_review": {
                    "status": "annual_fee_unavailable",
                    "reason": (
                        "Module-based distance learning. Published "
                        "module prices and a conditional upfront "
                        "programme total are available, but no "
                        "fixed annual fee is established."
                    ),
                },
                "route_evidence": evidence,
                "scholarship_status": "applicability_unconfirmed",
                "deposit_status": (
                    "distance_learning_payment_review_required"
                ),
            })

            notes = raw.get("display_notes", {})

            if not isinstance(notes, dict):
                notes = {}

            notes.update({
                "fee": (
                    "Annual tuition unavailable. This "
                    "distance-learning programme is charged "
                    "by module; a conditional £9,500 upfront "
                    "programme quote is published for 2026/27."
                ),
                "scholarship": (
                    "Scholarship applicability for this "
                    "distance-learning route has not yet "
                    "been reconciled."
                ),
                "deposit": (
                    "Confirm payment or deposit requirements "
                    "in your individual offer and "
                    "distance-learning payment schedule."
                ),
            })

            raw["display_notes"] = notes

            after["source"].update({
                "academic_year": ACADEMIC_YEAR,
                "source_url": FEE_URL,
                "source_course_name": "Agrifood Innovation",
                "source_course_code": SOURCE_COURSE_CODE,
                "fee_basis": "module_based",
            })

            overlap = Course.objects.filter(
                course_id=POSSIBLE_OVERLAP_ID,
                university_id="UK002",
            ).values(
                "course_id",
                "title",
                "duration",
                "start_month",
                "start_year",
                "is_active",
            ).first()

            # Keep complete evidence in the report, but do not treat
            # changing HTML hashes as a course or fee change.
            comparison_before = copy.deepcopy(before)
            comparison_after = copy.deepcopy(after)

            for comparison in (comparison_before, comparison_after):
                comparison["source"]["raw_fees"].pop(
                    "route_evidence",None
                )

            changed = comparison_before != comparison_after
            now = datetime.now(timezone.utc)

            folder = (
                Path(settings.BASE_DIR)
                / "logs"
                / "aberystwyth"
            )
            folder.mkdir(
                parents=True,
                exist_ok=True,
            )

            report_path = folder / (
                "agrifood-route-"
                + now.strftime("%Y%m%dT%H%M%S%fZ")
                + ".json"
            )

            report_path.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "mode": (
                            "apply_requested"
                            if options["apply"]
                            else "preview"
                        ),
                        "course_id": COURSE_ID,
                        "changed": changed,
                        "before": before,
                        "after": after,
                        "possible_overlap_unchanged": overlap,
                        "numeric_tuition_unchanged": str(
                            tuition.tuition_fee
                        ),
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.stdout.write(
                f"{COURSE_ID}: "
                f"{'update' if changed else 'unchanged'}"
            )

            for field in FIELDS:
                old = before["course"][field]
                new = after["course"][field]

                if old != new:
                    self.stdout.write(
                        f"  {field}: {old} -> {new}"
                    )

            self.stdout.write(
                f"Source course code: {SOURCE_COURSE_CODE}"
            )
            self.stdout.write(
                f"Fee schedule year: {ACADEMIC_YEAR}"
            )
            self.stdout.write(
                "20-credit online module: GBP "
                + prices["online_module_20_credits"]
            )
            self.stdout.write(
                "MSc dissertation: GBP "
                + prices["msc_dissertation_60_credits"]
            )
            self.stdout.write(
                "Conditional upfront programme quote: GBP "
                + prices["upfront_programme_quote"]
            )
            self.stdout.write(
                "Fixed annual tuition: unavailable"
            )
            self.stdout.write(
                f"{POSSIBLE_OVERLAP_ID}: "
                "remains pending; unchanged"
            )
            self.stdout.write(
                f"Proposed changes: {int(changed)}"
            )
            self.stdout.write(
                f"Report: {report_path}"
            )

            if not options["apply"]:
                self.stdout.write(
                    "Preview only. No database records changed."
                )
                return

            if changed:
                changed_fields = []

                for field, value in after["course"].items():
                    if getattr(course, field) != value:
                        setattr(course, field, value)
                        changed_fields.append(field)

                if changed_fields:
                    course.save(
                        update_fields=[
                            *changed_fields,
                            "updated_at",
                        ]
                    )

                for field, value in after["source"].items():
                    setattr(source, field, value)

                source.save(
                    update_fields=[
                        *after["source"].keys(),
                        "checked_at",
                    ]
                )

                TuitionUpdateAudit.objects.create(
                    tuition=tuition,
                    course_identifier=COURSE_ID,
                    residency="INTL",
                    before=before,
                    after=after,
                )

        self.stdout.write(
            self.style.SUCCESS(
                "D67ND3 route and published fee schedule saved."
                if changed
                else "Already current. No database updates needed."
            )
        )

        self.stdout.write(
            "Numeric tuition, stored scholarships and deposits "
            "unchanged. Annual fee remains unavailable."
        )