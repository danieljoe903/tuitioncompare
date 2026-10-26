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


LAW_ID = "C_UK002_25"
ARTS_ID = "C_UK002_86"

LAW_URL = (
    "https://courses.aber.ac.uk/postgraduate/"
    "M190-international-commercial-law/"
)
ARTS_URL = (
    "https://www.aber.ac.uk/en/study-schemes/2027-2028/"
    "schemes/scheme/?scheme=AB-WW48-BA-202709"
)
FEE_URL = (
    "https://www.aber.ac.uk/en/study-with-us/"
    "fees/postgrad/fees/"
)

COURSE_FIELDS = (
    "title",
    "level",
    "duration",
    "start_month",
    "start_year",
    "course_url",
    "is_active",
)


def verify_sources(session):
    law, law_evidence = download(session, LAW_URL)
    arts, arts_evidence = download(session, ARTS_URL)
    fees, fee_evidence = download(session, FEE_URL)

    law_heading = law.find("h1")
    if (
        law_heading is None
        or text(law_heading) != "International Commercial Law"
    ):
        raise ValueError("Law course title does not match.")

    law_text = text(law)

    for pattern in (
        r"\bM190-LLM\b",
        r"\bCourse duration\s+1\s+year\b",
        r"\bSeptember\s+start\s+2027\b",
    ):
        if not re.search(pattern, law_text, re.I):
            raise ValueError(
                f"Law course evidence does not match: {pattern}"
            )

    arts_heading = arts.find("h1")
    if (
        arts_heading is None
        or text(arts_heading) != "BA Creative Arts [WW48]"
    ):
        raise ValueError("Creative Arts scheme does not match.")

    if not re.search(
        r"\bLast intake year is\s+2024/2025\b",
        text(arts),
        re.I,
    ):
        raise ValueError(
            "Creative Arts last-intake evidence has changed."
        )

    current_year = False
    international_taught = False
    amounts = []
    law_classified = False

    for node in fees.find_all(["h2", "h3", "table"]):
        if node.name == "h2":
            current_year = (
                text(node).casefold()
                == "tuition fees for september 2027"
            )
            international_taught = False
            continue

        if node.name == "h3":
            label = text(node).casefold()
            international_taught = (
                current_year
                and "taught" in label
                and "international" in label
            )
            continue

        if not (current_year and international_taught):
            continue

        for row in node.find_all("tr"):
            cells = [
                text(cell)
                for cell in row.find_all(["th", "td"], recursive=False)
            ]
            if len(cells) < 2:
                continue

            if cells[0].casefold() != "arts and social science courses":
                continue

            if "law and criminology" in cells[1].casefold():
                law_classified = True

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

    if amounts != [Decimal("22115.00")] or not law_classified:
        raise ValueError(
            "The 2027 international taught-master's Law/Humanities "
            "classification and £22,115 fee must both be confirmed."
        )

    return {
        "law_course": law_evidence,
        "creative_arts_scheme": arts_evidence,
        "fee_schedule": fee_evidence,
    }


def snapshot(course, tuition, source):
    return {
        "course": {
            field: getattr(course, field)
            for field in COURSE_FIELDS
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
        "Preview or apply a reviewed current Law course replacement "
        "and deactivate the unsupported Creative Arts intake."
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

            plans = []

            for course_id in (LAW_ID, ARTS_ID):
                queryset = Course.objects.filter(
                    university_id="UK002",
                    course_id=course_id,
                )
                if options["apply"]:
                    queryset = queryset.select_for_update()

                try:
                    course = queryset.get()
                except Course.DoesNotExist as exc:
                    raise CommandError(
                        f"{course_id}: course not found."
                    ) from exc

                if course_id == LAW_ID:
                    if (
                        course.title not in {
                            "LLM International Business Law",
                            "LLM International Commercial Law",
                        }
                        or course.level != "PG"
                        or course.duration != "1 Year"
                        or not course.is_active
                    ):
                        raise CommandError(
                            "Law record differs from the reviewed route."
                        )

                    if Course.objects.filter(
                        university_id="UK002",
                        is_active=True,
                        course_url=LAW_URL,
                    ).exclude(course_id=LAW_ID).exists():
                        raise CommandError(
                            "Another active course already uses the "
                            "International Commercial Law URL."
                        )

                else:
                    if (
                        course.title != "BA (Hons) Creative Arts"
                        or course.level != "UG"
                        or course.start_year <= 2024
                    ):
                        raise CommandError(
                            "Creative Arts record differs from the "
                            "reviewed unsupported intake."
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
                        f"{course_id}: unique GBP international "
                        "tuition required."
                    )

                tuition = tuitions[0]
                sources = TuitionSource.objects.filter(tuition=tuition)
                if options["apply"]:
                    sources = sources.select_for_update()

                try:
                    source = sources.get()
                except TuitionSource.DoesNotExist as exc:
                    raise CommandError(
                        f"{course_id}: existing review metadata missing."
                    ) from exc

                if not isinstance(source.raw_fees, dict):
                    raise CommandError(
                        f"{course_id}: invalid source metadata."
                    )

                before = snapshot(course, tuition, source)
                after = copy.deepcopy(before)
                raw = after["source"]["raw_fees"]

                if course_id == LAW_ID:
                    after["course"].update({
                        "title": "LLM International Commercial Law",
                        "start_month": "September",
                        "start_year": 2027,
                        "course_url": LAW_URL,
                    })
                    after["tuition"]["tuition_fee"] = "22115.00"

                    raw.update({
                        "verified": True,
                        "fee_verification_status": "verified",
                        "academic_year": "2027/28",
                        "source_fee_category": "International",
                        "annual_tuition": "22115.00",
                        "currency": "GBP",
                        "fee_band": "Humanities",
                        "programme_fee_group": "masters",
                        "course_kind": "postgraduate_taught",
                        "course_source_url": LAW_URL,
                        "study_year": 1,
                        "fee_method": (
                            "reviewed_course_and_published_subject_band"
                        ),
                        "course_identity": {
                            "status": "reviewed_current_catalogue_replacement",
                            "official_title": "International Commercial Law",
                            "official_code": "M190",
                            "official_award": "LLM",
                            "source_url": LAW_URL,
                            "note": (
                                "Current catalogue replacement for the "
                                "stored listing; historical rename not asserted."
                            ),
                        },
                        "fee_review": {
                            "status": "resolved",
                            "reason": (
                                "Full-time taught LLM mapped to the published "
                                "2027 Law/Humanities international fee band."
                            ),
                        },
                        "fee_evidence": {
                            "course": evidence["law_course"],
                            "fees": evidence["fee_schedule"],
                        },
                        "scholarship_status": (
                            "pending_separate_reconciliation"
                        ),
                        "deposit_status": "offer_letter_amount_required",
                        "fee_categories": [{
                            "source_fee_category": "International",
                            "academic_year": "2027/28",
                            "currency": "GBP",
                            "annual_tuition": "22115.00",
                            "raw_annual_fee": "£22,115",
                            "annotation": (
                                "Full-time taught-master's Law/Humanities fee."
                            ),
                            "footnote_marker": "",
                            "amount_requires_review": False,
                        }],
                    })

                    notes = raw.get("display_notes", {})
                    if not isinstance(notes, dict):
                        notes = {}

                    notes.pop("fee", None)
                    notes["scholarship"] = (
                        "Scholarship applicability requires reconciliation."
                    )
                    notes["deposit"] = (
                        "Deposit amount is specified in your individual "
                        "offer letter."
                    )
                    raw["display_notes"] = notes

                    after["source"].update({
                        "academic_year": "2027/28",
                        "source_url": FEE_URL,
                        "source_course_name": "International Commercial Law",
                        "source_course_code": "M190",
                        "fee_basis": "annual_tuition",
                    })

                else:
                    after["course"]["is_active"] = False
                    raw["intake_review"] = {
                        "status": "stored_intake_after_last_published_intake",
                        "last_intake_academic_year": "2024/25",
                        "source_url": ARTS_URL,
                        "evidence": evidence["creative_arts_scheme"],
                        "action": "deactivate_stored_new_entrant_listing",
                        "note": (
                            "The stored intake is later than the scheme's "
                            "published last intake. Historical course and "
                            "tuition records are retained."
                        ),
                    }

                plans.append({
                    "course_id": course_id,
                    "course": course,
                    "tuition": tuition,
                    "source": source,
                    "before": before,
                    "after": after,
                    "changed": before != after,
                })

            now = datetime.now(timezone.utc)
            folder = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
            folder.mkdir(parents=True, exist_ok=True)
            report_path = folder / (
                "law-creative-arts-"
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
                    "evidence": evidence,
                    "records": [
                        {
                            key: plan[key]
                            for key in (
                                "course_id", "changed", "before", "after"
                            )
                        }
                        for plan in plans
                    ],
                }, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            for plan in plans:
                self.stdout.write(
                    f"{plan['course_id']}: "
                    f"{'update' if plan['changed'] else 'unchanged'}"
                )

                for field in COURSE_FIELDS:
                    old = plan["before"]["course"][field]
                    new = plan["after"]["course"][field]
                    if old != new:
                        self.stdout.write(f"  {field}: {old} -> {new}")

                if plan["course_id"] == LAW_ID:
                    self.stdout.write(
                        "  International tuition: GBP "
                        f"{plan['before']['tuition']['tuition_fee']} "
                        "-> 22115.00; 2027/28"
                    )
                else:
                    self.stdout.write(
                        "  Last published intake: 2024/25. "
                        "No fee imported; historical records retained."
                    )

            self.stdout.write(
                "\nProposed changes: "
                + str(sum(plan["changed"] for plan in plans))
            )
            self.stdout.write(f"Report: {report_path}")

            if not options["apply"]:
                self.stdout.write(
                    "Preview only. No database records changed."
                )
                return

            for plan in plans:
                if not plan["changed"]:
                    continue

                course = plan["course"]
                changed_fields = []

                for field, value in plan["after"]["course"].items():
                    if getattr(course, field) != value:
                        setattr(course, field, value)
                        changed_fields.append(field)

                if changed_fields:
                    course.save(
                        update_fields=[*changed_fields, "updated_at"]
                    )

                tuition = plan["tuition"]
                if plan["course_id"] == LAW_ID:
                    tuition.tuition_fee = Decimal("22115.00")
                    tuition.save(update_fields=["tuition_fee"])

                source = plan["source"]
                for field, value in plan["after"]["source"].items():
                    setattr(source, field, value)

                source.save(
                    update_fields=[
                        *plan["after"]["source"].keys(),
                        "checked_at",
                    ]
                )

                TuitionUpdateAudit.objects.create(
                    tuition=tuition,
                    course_identifier=plan["course_id"],
                    residency="INTL",
                    before=plan["before"],
                    after=plan["after"],
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Law replacement/fee and Creative Arts intake correction saved."
            )
        )
        self.stdout.write(
            "Stored scholarships and deposits unchanged. "
            "No course or tuition records deleted."
        )