import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    Course,
    DepositPolicy,
    Scholarship,
    ScholarshipRule,
    Tuition,
    TuitionSource,
    University,
)

from .import_aberystwyth_fees import (
    REVIEWED_COURSES as FIRST_BATCH,
)
from .import_aberystwyth_batch_two import (
    REVIEWED_COURSES as SECOND_BATCH,
)


YEAR = "2027/28"

SCHOLARSHIP_URL = (
    "https://www.aber.ac.uk/en/study-with-us/international/"
    "scholarships/global-citizens-scholarship/"
)

DEPOSIT_URL = (
    "https://www.aber.ac.uk/en/study-with-us/"
    "pg-studies/apply/isd/"
)

# Expected published scholarship-table values:
# (gross tuition, scholarship, tuition after scholarship)
EXPECTED = {
    ("UG", "Humanities"): ("20400.00", "3000.00", "17400.00"),
    ("UG", "Sciences"): ("23555.00", "4500.00", "19055.00"),
    ("PG", "Humanities"): ("22115.00", "3000.00", "19115.00"),
    ("PG", "Sciences"): ("23820.00", "3800.00", "20020.00"),
}

# Tuition verified in our two import batches.
VERIFIED_TUITION = {
    ("UG", "Humanities"): Decimal("20400.00"),
    ("UG", "Sciences"): Decimal("23255.00"),
    ("PG", "Humanities"): Decimal("22115.00"),
    ("PG", "Sciences"): Decimal("23820.00"),
}


def text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def download(session, url):
    response = session.get(url, timeout=(30, 60))
    response.raise_for_status()

    if response.url.rstrip("/") != url.rstrip("/"):
        raise ValueError(f"Unexpected redirect: {response.url}")

    soup = BeautifulSoup(
        response.content.decode("utf-8-sig"),
        "html.parser",
    )

    main = (
        soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.body
    )

    if main is None:
        raise ValueError(f"No readable content: {url}")

    return response.content, main


def amount(raw):
    """Parse one GBP amount, allowing text after the amount."""
    import re

    match = re.match(
        r"^\s*£\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d{2})?(?=\s|$)",
        raw,
    )

    if not match:
        raise ValueError(f"Unsupported published amount: {raw!r}")

    try:
        value = Decimal(
            match.group(1).replace(",", "")
            + (match.group(2) or ".00")
        )
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount: {raw!r}") from exc

    if not value.is_finite() or value <= 0:
        raise ValueError(f"Invalid amount: {raw!r}")

    return value.quantize(Decimal("0.01"))


def parse_scholarships(main):
    content = text(main)
    lower = content.casefold()

    required = (
        "2027/28 onwards",
        "international fee-paying",
        "1st august",
        "received an offer",
        "external",
        "distance learning",
        "overseas",
        "automatically",
    )

    for phrase in required:
        if phrase not in lower:
            raise ValueError(
                f"Scholarship terms changed: {phrase!r} missing."
            )

    matches = {"UG": [], "PG": []}

    for table in main.find_all("table"):
        rows = [
            [
                " ".join(
                    cell.get_text(" ", strip=True).split()
                )
                for cell in row.find_all(["th", "td"])
            ]
            for row in table.find_all("tr")
        ]

        if not rows:
            continue

        if not any(
            YEAR in cell
            for row in rows[:2]
            for cell in row
        ):
            continue

        band_rows = {}

        for row in rows:
            if len(row) < 4:
                continue

            label = row[0].strip().casefold()

            if label not in ("humanities", "sciences"):
                continue

            band = (
                "Humanities"
                if label == "humanities"
                else "Sciences"
            )

            if band in band_rows:
                raise ValueError(
                    f"Duplicate {band} row in a scholarship table."
                )

            band_rows[band] = tuple(
                str(amount(cell))
                for cell in row[1:4]
            )

        if set(band_rows) != {"Humanities", "Sciences"}:
            continue

        for level in ("UG", "PG"):
            expected_rows = {
                band: EXPECTED[(level, band)]
                for band in ("Humanities", "Sciences")
            }

            if band_rows == expected_rows:
                matches[level].append(band_rows)

    published = {}

    for level in ("UG", "PG"):
        if len(matches[level]) != 1:
            raise ValueError(
                f"Expected one reviewed {level} scholarship table; "
                f"found {len(matches[level])}. "
                "The published table values or structure need review."
            )

        for band, values in matches[level][0].items():
            gross, award, net = values

            if Decimal(gross) - Decimal(award) != Decimal(net):
                raise ValueError(
                    f"{level} {band}: published arithmetic disagrees."
                )

            published[(level, band)] = {
                "gross": gross,
                "award": award,
                "net": net,
            }

    return published, content

def reviewed_courses():
    courses = {}

    for course_id, reviewed in FIRST_BATCH.items():
        title, group, _department = reviewed
        level = "UG" if group == "bachelors" else "PG"
        courses[course_id] = (title, level, "Humanities")

    for suffix, reviewed in SECOND_BATCH.items():
        title, group, band, _department = reviewed
        course_id = f"C_UK002_{suffix}"

        if course_id in courses:
            raise CommandError(f"Duplicate reviewed mapping: {course_id}")

        level = "UG" if group == "bachelors" else "PG"
        courses[course_id] = (title, level, band)

    return courses


def rule_data(course_ids, published, verified):
    rules = {
        "benefit": "tuition",
        "kind": "fixed",
        "value": published["award"],
        "currency": "GBP",
        "academic_year": YEAR,
        "study_years": [1],
        "fee_basis_confirmed": verified,
        "percentage_basis": "original_tuition",
        "award_status": "conditional",
        "stacking": "exclusive",
        "compatible_ids": [],
        "conditions": {
            "course_id": sorted(course_ids),
            "fee_status": ["INTL"],
            "new_entrant": [True],
            "academic_conditions_met": [True],
            "offer_received": [True],
            "applied_by_deadline": [True],
            "external_funding": [False],
        },
        "application_deadline": "2027-08-01",
        "scope_note": (
            "First compared study year only. Restricted to reviewed "
            "campus course IDs, programme level and fee band."
        ),
        "combination_note": (
            "Other tuition awards require separately verified combination "
            "rules. Accommodation benefits are excluded."
        ),
        "published_scholarship_table": published,
    }

    if not verified:
        rules["review_reason"] = (
            "Undergraduate science gross tuition differs between the "
            "scholarship table (£23,555) and verified tuition sources "
            "(£23,255). Record the published award but do not deduct it "
            "until its applicable fee basis is confirmed."
        )

    return rules


class Command(BaseCommand):
    help = (
        "Reconcile Aberystwyth scholarship rules for the first two "
        "reviewed tuition batches and save the deposit policy."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
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
                    "TuitionCompareSourceInspector/1.0"
                )
                session.mount(
                    "https://",
                    HTTPAdapter(max_retries=retry),
                )

                scholarship_html, scholarship_main = download(
                    session, SCHOLARSHIP_URL
                )
                published, scholarship_text = parse_scholarships(
                    scholarship_main
                )

                deposit_html, deposit_main = download(
                    session, DEPOSIT_URL
                )
                deposit_text = text(deposit_main)

                if (
                    "details will be given in your offer letter"
                    not in deposit_text.casefold()
                    or "exemptions" not in deposit_text.casefold()
                ):
                    raise ValueError("Offer-letter deposit policy changed.")

        except (
            requests.RequestException,
            UnicodeDecodeError,
            ValueError,
            ArithmeticError,
        ) as exc:
            raise CommandError(f"Source verification stopped: {exc}") from exc

        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        directory = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aberystwyth"
            / f"awards-{stamp}"
        )
        directory.mkdir(parents=True, exist_ok=True)

        (directory / "scholarship.html").write_bytes(scholarship_html)
        (directory / "deposit.html").write_bytes(deposit_html)

        scopes = {key: [] for key in EXPECTED}
        proposals = []

        deposit_after = {
            "currency": "GBP",
            "rules": {
                "schema_version": 1,
                "kind": "offer_letter",
                "fee_status": "INTL",
                "payment_stage": "before_cas",
                "amount": None,
                "percentage": None,
                "currency": "GBP",
                "amount_requires_individual_confirmation": True,
                "exemptions_require_confirmation": True,
                "note": (
                    "Where a deposit is required, the amount is specified "
                    "in the applicant's offer letter. It forms part of "
                    "tuition payments. Exemptions require confirmation."
                ),
            },
            "source_url": DEPOSIT_URL,
            "verified": True,
        }

        with transaction.atomic():
            try:
                university = University.objects.select_for_update().get(
                    pk="UK002", country="UK"
                )
            except University.DoesNotExist as exc:
                raise CommandError("Aberystwyth UK002 not found.") from exc

            if university.name.strip().casefold() != "aberystwyth university":
                raise CommandError("University identity mismatch.")

            # Validate every course before changing any scholarship.
            for course_id, reviewed in reviewed_courses().items():
                title, level, band = reviewed

                course = Course.objects.select_for_update().filter(
                    pk=course_id,
                    university=university,
                    is_active=True,
                    start_month="September",
                    start_year=2027,
                ).first()

                if course is None:
                    raise CommandError(
                        f"{course_id}: apply both tuition batches first."
                    )

                allowed_levels = {"UG"} if level == "UG" else {"PG", "PGT"}
                if course.level not in allowed_levels:
                    raise CommandError(f"{course_id}: course level changed.")

                tuitions = list(
                    Tuition.objects.select_for_update().filter(
                        course=course, residency="INTL"
                    )
                )

                if len(tuitions) != 1:
                    raise CommandError(
                        f"{course_id}: unique international tuition required."
                    )

                tuition = tuitions[0]
                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )
                raw = source.raw_fees if source else {}

                if (
                    source is None
                    or not isinstance(raw, dict)
                    or source.academic_year != YEAR
                    or source.fee_basis != "annual_tuition"
                    or source.source_course_name != title
                    or raw.get("verified") is not True
                    or raw.get("fee_band") != band
                    or raw.get("fee_method")
                    != "reviewed_course_and_published_band"
                    or tuition.currency != "GBP"
                    or Decimal(str(tuition.tuition_fee))
                    != VERIFIED_TUITION[(level, band)]
                ):
                    raise CommandError(
                        f"{course_id}: reviewed tuition evidence changed."
                    )

                scopes[(level, band)].append(course_id)

            for (level, band), course_ids in scopes.items():
                values = published[(level, band)]
                verified = (
                    Decimal(values["gross"])
                    == VERIFIED_TUITION[(level, band)]
                )

                name = (
                    f"Global Citizens Scholarship — "
                    f"{band} {level} ({YEAR})"
                )

                matches = list(
                    Scholarship.objects.select_for_update().filter(
                        university=university,
                        name=name,
                        level=level,
                    )
                )

                if len(matches) > 1:
                    raise CommandError(f"Duplicate scholarships: {name}")

                award = matches[0] if matches else None
                existing_rule = (
                    ScholarshipRule.objects.select_for_update()
                    .filter(scholarship=award)
                    .first()
                    if award else None
                )

                award_fields = {
                    "award_type": "Tuition Reduction",
                    "award_value": values["award"],
                    "currency": "GBP",
                    "deadline": None,
                    "eligibility": (
                        f"New international {level} students on reviewed "
                        f"{band.lower()} campus courses for {YEAR}."
                    ),
                    "requirements": (
                        "Offer received; academic entry requirements met; "
                        "course application by 1 August 2027; no external "
                        "scholarship, funding or bursary."
                    ),
                    "description": (
                        f"Published annual tuition award: GBP "
                        f"{values['award']}. First study year modelled. "
                        + (
                            "Eligibility must be confirmed."
                            if verified
                            else "Fee-source discrepancy requires review; "
                                 "do not deduct automatically."
                        )
                    ),
                    "url": SCHOLARSHIP_URL,
                }

                before = {
                    "award": (
                        {key: getattr(award, key) for key in award_fields}
                        if award else None
                    ),
                    "rule": (
                        {
                            "verified": existing_rule.verified,
                            "source_url": existing_rule.source_url,
                            "rules": existing_rule.rules,
                        }
                        if existing_rule else None
                    ),
                }

                after = {
                    "award": award_fields,
                    "rule": {
                        "verified": verified,
                        "source_url": SCHOLARSHIP_URL,
                        "rules": rule_data(course_ids, values, verified),
                    },
                }

                proposals.append({
                    "type": "scholarship",
                    "name": name,
                    "level": level,
                    "band": band,
                    "course_count": len(course_ids),
                    "verified": verified,
                    "changed": before != after,
                    "before": before,
                    "after": after,
                })

            policy = DepositPolicy.objects.select_for_update().filter(
                university=university,
                academic_year=YEAR,
            ).first()

            deposit_before = (
                {key: getattr(policy, key) for key in deposit_after}
                if policy else None
            )

            proposals.append({
                "type": "deposit_policy",
                "changed": deposit_before != deposit_after,
                "before": deposit_before,
                "after": deposit_after,
            })

            legacy_rules = list(
                ScholarshipRule.objects.select_for_update().filter(
                    scholarship_id__in=[87, 88],
                    scholarship__university=university,
                    verified=True,
                ).values("pk", "scholarship_id")
            )

            report_path = directory / "reconciliation.json"
            report_path.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "university_id": "UK002",
                        "academic_year": YEAR,
                        "mode": "apply" if options["apply"] else "preview",
                        "proposals": proposals,
                        "legacy_rules_to_mark_unverified": legacy_rules,
                        "tuition_prices_changed": False,
                        "stored_discounts_changed": False,
                        "stored_deposits_changed": False,
                        "scholarship_source_sha256": hashlib.sha256(
                            scholarship_text.encode("utf-8")
                        ).hexdigest(),
                    },
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )

            if options["apply"]:
                for proposal in proposals:
                    if (
                        proposal["type"] != "scholarship"
                        or not proposal["changed"]
                    ):
                        continue

                    award, _created = Scholarship.objects.update_or_create(
                        university=university,
                        name=proposal["name"],
                        level=proposal["level"],
                        defaults=proposal["after"]["award"],
                    )

                    ScholarshipRule.objects.update_or_create(
                        scholarship=award,
                        defaults={
                            **proposal["after"]["rule"],
                            "reviewed_at": now,
                        },
                    )

                DepositPolicy.objects.update_or_create(
                    university=university,
                    academic_year=YEAR,
                    defaults={
                        **deposit_after,
                        "source_text": deposit_text,
                        "source_sha256": hashlib.sha256(
                            deposit_text.encode("utf-8")
                        ).hexdigest(),
                        "checked_at": now,
                    },
                )

                ScholarshipRule.objects.filter(
                    pk__in=[row["pk"] for row in legacy_rules]
                ).update(verified=False, reviewed_at=now)

        for proposal in proposals:
            label = proposal["type"]

            if label == "scholarship":
                label = (
                    f"{proposal['level']} {proposal['band']}: "
                    f"{proposal['course_count']} courses; "
                    f"{'verified' if proposal['verified'] else 'REVIEW REQUIRED'}"
                )

            self.stdout.write(
                f"{label} — "
                f"{'update' if proposal['changed'] else 'unchanged'}"
            )

        self.stdout.write(f"Report: {report_path}")
        self.stdout.write(
            "Scholarship rules and deposit policy saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Tuition prices and stored discount/deposit amounts unchanged."
        )