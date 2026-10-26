import copy
import json
from datetime import datetime, timezone
from decimal import Decimal

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from pathlib import Path

from app.models import (
    Course,
    ScholarshipRule,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)

from .import_aberystwyth_welsh_education_fees import download, text


COURSE_ID = "C_UK002_25"
YEAR = "2027/28"

COURSE_URL = (
    "https://courses.aber.ac.uk/postgraduate/"
    "M190-international-commercial-law/"
)
SCHOLARSHIP_URL = (
    "https://www.aber.ac.uk/en/study-with-us/international/"
    "scholarships/global-citizens-scholarship/"
)
RULE_NAME = "Global Citizens Scholarship — Humanities PG (2027/28)"

REQUIRED_CONDITIONS = {
    "fee_status": ["INTL"],
    "new_entrant": [True],
    "academic_conditions_met": [True],
    "offer_received": [True],
    "applied_by_deadline": [True],
    "external_funding": [False],
}


def verify_award(soup):
    heading = soup.find("h1")
    if heading is None or text(heading) != "Global Citizens Scholarship":
        raise ValueError("Wrong scholarship page.")

    matches = []

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = [
                text(cell)
                for cell in row.find_all(["th", "td"], recursive=False)
            ]

            if len(cells) < 4 or cells[0].casefold() != "humanities":
                continue

            values = [
                value.replace(",", "").replace(" ", "")
                for value in cells[1:4]
            ]

            if (
                values[0] == "£22115"
                and values[1] == "£3000"
                and values[2].startswith("£19115")
            ):
                matches.append(cells)

    if len(matches) != 1:
        raise ValueError(
            "A unique postgraduate Humanities row "
            "£22,115 / £3,000 / £19,115 is required."
        )

    content = text(soup).casefold()

    for phrase in (
        "new student from the academic year 2027/28 onwards",
        "international fee-paying",
        "academic entry requirements",
        "received an offer",
        "external scholarships",
        "1st august",
        "distance learning",
        "law and criminology",
    ):
        if phrase not in content:
            raise ValueError(
                f"Published scholarship terms require review: {phrase!r}"
            )

    return matches[0]


class Command(BaseCommand):
    help = (
        "Add International Commercial Law to the verified "
        "2027/28 Humanities postgraduate scholarship scope."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

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
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET"],
                    respect_retry_after_header=True,
                )
                session.mount(
                    "https://",
                    HTTPAdapter(max_retries=retry),
                )

                page, evidence = download(session, SCHOLARSHIP_URL)
                published_row = verify_award(page)

        except (requests.RequestException, ValueError) as exc:
            raise CommandError(
                f"Source verification stopped: {exc}. "
                "No database records changed."
            ) from exc

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
                raise CommandError("Active Law course not found.") from exc

            if (
                course.title != "LLM International Commercial Law"
                or course.level != "PG"
                or course.duration != "1 Year"
                or course.start_month != "September"
                or course.start_year != 2027
                or course.course_url.rstrip("/") != COURSE_URL.rstrip("/")
            ):
                raise CommandError(
                    "Law course does not match the corrected route."
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
                or Decimal(str(tuitions[0].tuition_fee))
                != Decimal("22115.00")
            ):
                raise CommandError(
                    "A unique GBP 22,115 international tuition is required."
                )

            tuition = tuitions[0]

            sources = TuitionSource.objects.filter(tuition=tuition)
            if options["apply"]:
                sources = sources.select_for_update()

            try:
                source = sources.get()
            except TuitionSource.DoesNotExist as exc:
                raise CommandError("Verified fee source is missing.") from exc

            raw = source.raw_fees
            if (
                not isinstance(raw, dict)
                or raw.get("verified") is not True
                or source.academic_year != YEAR
                or source.source_course_code != "M190"
                or raw.get("fee_band") != "Humanities"
                or raw.get("course_kind") != "postgraduate_taught"
            ):
                raise CommandError(
                    "Verified 2027/28 taught Law fee metadata is required."
                )

            rules = ScholarshipRule.objects.filter(
                scholarship__university_id="UK002",
                scholarship__name=RULE_NAME,
                scholarship__level="PG",
            )
            if options["apply"]:
                rules = rules.select_for_update()
            rules = list(rules)

            if len(rules) != 1:
                raise CommandError(
                    "A unique existing Humanities postgraduate rule "
                    "is required."
                )

            rule = rules[0]
            saved = rule.rules

            if (
                rule.verified is not True
                or rule.source_url.rstrip("/")
                != SCHOLARSHIP_URL.rstrip("/")
                or not isinstance(saved, dict)
            ):
                raise CommandError("Existing award rule is not verified.")

            if (
                saved.get("kind") != "fixed"
                or saved.get("value") not in ("3000", "3000.00")
                or saved.get("currency") != "GBP"
                or saved.get("benefit") != "tuition"
                or saved.get("academic_year") != YEAR
                or saved.get("study_years") != [1]
                or saved.get("fee_basis_confirmed") is not True
                or saved.get("award_status") != "conditional"
                or saved.get("application_deadline") != "2027-08-01"
                or saved.get("published_scholarship_table") != {
                    "gross": "22115.00",
                    "award": "3000.00",
                    "net": "19115.00",
                }
            ):
                raise CommandError(
                    "Existing award configuration differs from the "
                    "reviewed Humanities postgraduate rule."
                )

            conditions = saved.get("conditions")
            if not isinstance(conditions, dict):
                raise CommandError("Invalid award conditions.")

            for key, expected in REQUIRED_CONDITIONS.items():
                if conditions.get(key) != expected:
                    raise CommandError(
                        f"Unexpected eligibility condition: {key}."
                    )

            course_ids = conditions.get("course_id")
            if (
                not isinstance(course_ids, list)
                or not course_ids
                or not all(isinstance(value, str) for value in course_ids)
            ):
                raise CommandError("Invalid existing course scope.")

            before = {
                "scholarship_rule_id": rule.pk,
                "rules": copy.deepcopy(saved),
                "raw_fees": copy.deepcopy(raw),
            }

            updated_rule = copy.deepcopy(saved)
            updated_rule["conditions"]["course_id"] = sorted(
                set(course_ids) | {COURSE_ID}
            )

            updated_raw = copy.deepcopy(raw)
            updated_raw["scholarship_status"] = (
                "verified_rule_eligibility_required"
            )
            updated_raw["scholarship_review"] = {
                "status": "verified_rule_eligibility_required",
                "scholarship_rule_id": rule.pk,
                "source_url": SCHOLARSHIP_URL,
                "academic_year": YEAR,
                "gross": "22115.00",
                "award": "3000.00",
                "net_if_eligible": "19115.00",
                "study_year": 1,
                "note": (
                    "Humanities taught-postgraduate award. Student "
                    "eligibility must be confirmed before deduction."
                ),
            }
            updated_raw["deposit_status"] = (
                "offer_letter_amount_required"
            )

            notes = updated_raw.get("display_notes", {})
            if not isinstance(notes, dict):
                notes = {}

            notes["scholarship"] = (
                "£3,000 scholarship if published eligibility conditions "
                "are met; estimated net tuition £19,115."
            )
            notes["deposit"] = (
                "Deposit amount is specified in your individual offer letter."
            )
            updated_raw["display_notes"] = notes

            after = {
                "scholarship_rule_id": rule.pk,
                "rules": updated_rule,
                "raw_fees": updated_raw,
            }
            changed = before != after

            now = datetime.now(timezone.utc)
            folder = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
            folder.mkdir(parents=True, exist_ok=True)
            report_path = folder / (
                "law-award-"
                + now.strftime("%Y%m%dT%H%M%S%fZ")
                + ".json"
            )

            report_path.write_text(
                json.dumps({
                    "generated_at": now.isoformat(),
                    "mode": (
                        "apply_requested"
                        if options["apply"]
                        else "preview"
                    ),
                    "course_id": COURSE_ID,
                    "changed": changed,
                    "evidence": evidence,
                    "published_row": published_row,
                    "before": before,
                    "after": after,
                }, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            self.stdout.write(
                f"{COURSE_ID}: "
                f"{'update' if changed else 'unchanged'}"
            )
            self.stdout.write(
                "Conditional scholarship: GBP 3000.00"
            )
            self.stdout.write(
                "Net tuition if eligible: GBP 19115.00"
            )
            self.stdout.write(
                "Humanities PG course scope: "
                f"{len(updated_rule['conditions']['course_id'])}"
            )
            self.stdout.write(f"Proposed changes: {int(changed)}")
            self.stdout.write(f"Report: {report_path}")

            if not options["apply"]:
                self.stdout.write(
                    "Preview only. No database records changed."
                )
                return

            if changed:
                rule.rules = updated_rule
                rule.save(update_fields=["rules", "reviewed_at"])

                source.raw_fees = updated_raw
                source.save(update_fields=["raw_fees", "checked_at"])

                TuitionUpdateAudit.objects.create(
                    tuition=tuition,
                    course_identifier=COURSE_ID,
                    residency="INTL",
                    before=before,
                    after=after,
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Law scholarship scope and course metadata saved."
                if changed
                else "Already current. No database updates needed."
            )
        )
        self.stdout.write(
            "Gross tuition, stored discounts and numeric deposits unchanged."
        )