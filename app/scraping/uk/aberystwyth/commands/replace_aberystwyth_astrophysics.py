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
from django.db.models import Q

from app.models import (
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)

from app.scraping.uk.aberystwyth.commands.import_aberystwyth_welsh_education_fees import download, text


COURSE_ID = "C_UK002_83"
OLD_TITLE = "BSc (Hons) Physics with Astronomy"
NEW_TITLE = "BSc (Hons) Astrophysics"

COURSE_URL = (
    "https://courses.aber.ac.uk/undergraduate/astrophysics-degree/"
)
FEE_URL = (
    "https://www.aber.ac.uk/en/study-with-us/"
    "fees/undergrad/tuition-fees/"
)

YEAR = "2027/28"
FEE = Decimal("23255.00")

FIELDS = (
    "title",
    "level",
    "duration",
    "start_month",
    "start_year",
    "course_url",
    "ucas_cao_code",
)


def verify_sources(session):
    course, course_evidence = download(session, COURSE_URL)
    fees, fee_evidence = download(session, FEE_URL)

    heading = course.find("h1")
    if heading is None or text(heading) != "Astrophysics":
        raise ValueError("Official course title does not match.")

    content = text(course)

    for pattern in (
        r"\bAstrophysics\s+Code\s+F510\b",
        r"\bCourse duration\s+3\s+years\b",
        r"\bSeptember\s+start\s+2027\b",
        r"\bDepartment of Physics\b",
    ):
        if not re.search(pattern, content, re.I):
            raise ValueError(
                f"Required Astrophysics evidence missing: {pattern}"
            )

    current_year = False
    international_section = False
    amounts = []
    physics_classified = False

    for node in fees.find_all(["h2", "h3", "table"]):
        if node.name == "h2":
            current_year = (
                text(node).casefold()
                == "tuition fees for september 2027"
            )
            international_section = False
            continue

        if node.name == "h3":
            international_section = (
                current_year
                and "fee information for international students"
                in text(node).casefold()
            )
            continue

        if not (current_year and international_section):
            continue

        for row in node.find_all("tr"):
            cells = [
                text(cell)
                for cell in row.find_all(["th", "td"], recursive=False)
            ]
            if len(cells) < 2:
                continue

            label = cells[0].casefold()

            if label == "science courses":
                if re.search(r"\bPhysics\b", cells[1], re.I):
                    physics_classified = True

            if label == "full-time science":
                match = re.fullmatch(
                    r"£\s*([\d,]+(?:\.\d{2})?)",
                    cells[1],
                )
                if match:
                    amounts.append(
                        Decimal(
                            match.group(1).replace(",", "")
                        ).quantize(Decimal("0.01"))
                    )

    if amounts != [FEE] or not physics_classified:
        raise ValueError(
            "Expected one £23,255 international science fee "
            "and an explicit Physics science classification."
        )

    return {
        "course": course_evidence,
        "fee_schedule": fee_evidence,
    }


def snapshot(course, tuition, source):
    return {
        "course": {
            field: getattr(course, field)
            for field in FIELDS
        },
        "tuition": {
            "tuition_fee": str(
                Decimal(str(tuition.tuition_fee)).quantize(
                    Decimal("0.01")
                )
            ),
            "currency": tuition.currency,
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
        "Replace the stored Physics with Astronomy listing with "
        "the accepted current Astrophysics route and verified fee."
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
                evidence = verify_sources(session)

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
                raise CommandError("Active course not found.") from exc

            if (
                course.title not in {OLD_TITLE, NEW_TITLE}
                or course.level != "UG"
                or course.duration != "3 Years"
            ):
                raise CommandError(
                    "Stored course differs from the reviewed replacement."
                )

            duplicates = Course.objects.filter(
                university_id="UK002",
                is_active=True,
            ).exclude(course_id=COURSE_ID).filter(
                Q(course_url=COURSE_URL)
                | Q(ucas_cao_code="F510")
                | Q(title=NEW_TITLE)
            )

            if duplicates.exists():
                raise CommandError(
                    "Another active record already represents Astrophysics. "
                    "Review duplication before replacement."
                )

            tuitions = Tuition.objects.filter(
                course=course,
                residency="INTL",
            )
            if options["apply"]:
                tuitions = tuitions.select_for_update()
            tuitions = list(tuitions)

            if len(tuitions) != 1 or tuitions[0].currency != "GBP":
                raise CommandError(
                    "A unique GBP international tuition is required."
                )

            tuition = tuitions[0]
            sources = TuitionSource.objects.filter(tuition=tuition)
            if options["apply"]:
                sources = sources.select_for_update()

            try:
                source = sources.get()
            except TuitionSource.DoesNotExist as exc:
                raise CommandError(
                    "Existing course review metadata is missing."
                ) from exc

            if not isinstance(source.raw_fees, dict):
                raise CommandError("Invalid source metadata.")

            already_replaced = (
                source.raw_fees.get("course_identity", {}).get("status")
                == "accepted_current_catalogue_replacement"
                and source.source_course_code == "F510"
            )

            if (
                source.raw_fees.get("verified") is True
                and not already_replaced
            ):
                raise CommandError(
                    "An unrelated verified fee already exists. "
                    "No overwrite performed."
                )

            before = snapshot(course, tuition, source)
            after = copy.deepcopy(before)

            after["course"].update({
                "title": NEW_TITLE,
                "level": "UG",
                "duration": "3 Years",
                "start_month": "September",
                "start_year": 2027,
                "course_url": COURSE_URL,
                "ucas_cao_code": "F510",
            })
            after["tuition"]["tuition_fee"] = str(FEE)

            raw = after["source"]["raw_fees"]
            raw.update({
                "verified": True,
                "fee_verification_status": "verified",
                "academic_year": YEAR,
                "source_fee_category": "International",
                "annual_tuition": str(FEE),
                "currency": "GBP",
                "fee_band": "Sciences",
                "programme_fee_group": "bachelors",
                "course_kind": "undergraduate",
                "course_source_url": COURSE_URL,
                "study_year": 1,
                "fee_method": (
                    "accepted_replacement_and_published_subject_band"
                ),
                "course_identity": {
                    "status": "accepted_current_catalogue_replacement",
                    "replaces_title": OLD_TITLE,
                    "official_title": "Astrophysics",
                    "official_code": "F510",
                    "official_award": "BSC",
                    "source_url": COURSE_URL,
                    "note": (
                        "User accepted replacing the stored listing with "
                        "the current Astrophysics route. This does not "
                        "assert historical course equivalence."
                    ),
                },
                "fee_review": {
                    "status": "resolved",
                    "reason": (
                        "Astrophysics is taught in Physics, classified "
                        "under the published undergraduate science fee."
                    ),
                },
                "fee_evidence": evidence,
                "fee_categories": [{
                    "source_fee_category": "International",
                    "academic_year": YEAR,
                    "currency": "GBP",
                    "annual_tuition": str(FEE),
                    "raw_annual_fee": "£23,255",
                    "annotation": (
                        "Annual full-time undergraduate science tuition."
                    ),
                    "footnote_marker": "",
                    "amount_requires_review": False,
                }],
            })

            notes = raw.get("display_notes", {})
            if not isinstance(notes, dict):
                notes = {}

            notes.pop("fee", None)

            # Preserve later scholarship reconciliation on repeat imports.
            if not already_replaced:
                raw["scholarship_status"] = (
                    "pending_separate_reconciliation"
                )
                raw["scholarship_review"] = {
                    "status": "pending",
                    "reason": (
                        "Scholarship applicability for the replacement "
                        "course requires separate reconciliation."
                    ),
                }
                notes["scholarship"] = (
                    "Scholarship eligibility has not yet been confirmed "
                    "for this replacement course."
                )

            raw["deposit_status"] = "offer_letter_amount_required"
            notes["deposit"] = (
                "Deposit amount is specified in your individual offer letter."
            )
            raw["display_notes"] = notes

            after["source"].update({
                "academic_year": YEAR,
                "source_url": FEE_URL,
                "source_course_name": "Astrophysics",
                "source_course_code": "F510",
                "fee_basis": "annual_tuition",
            })

            changed = before != after
            now = datetime.now(timezone.utc)
            folder = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
            folder.mkdir(parents=True, exist_ok=True)

            report_path = folder / (
                "astrophysics-replacement-"
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
                    "before": before,
                    "after": after,
                }, indent=2, ensure_ascii=False),
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
                    self.stdout.write(f"  {field}: {old} -> {new}")

            self.stdout.write(
                f"International tuition: GBP "
                f"{before['tuition']['tuition_fee']} -> {FEE}; {YEAR}"
            )
            self.stdout.write(f"Proposed changes: {int(changed)}")
            self.stdout.write(f"Report: {report_path}")

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
                        update_fields=[*changed_fields, "updated_at"]
                    )

                tuition.tuition_fee = FEE
                tuition.save(update_fields=["tuition_fee"])

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
                "Accepted Astrophysics replacement and verified fee saved."
                if changed
                else "Already current. No database updates needed."
            )
        )
        self.stdout.write(
            "Stored scholarships and numeric deposits unchanged. "
            "Other unresolved courses were not modified."
        )