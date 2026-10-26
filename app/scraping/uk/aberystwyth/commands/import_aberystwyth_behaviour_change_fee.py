import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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


COURSE_ID = "C_UK002_91"
YEAR = "2027/28"

COURSE_URL = (
    "https://courses.aber.ac.uk/postgraduate/"
    "C801-behaviour-change/"
)
DEPARTMENT_URL = (
    "https://www.aber.ac.uk/en/study-schemes/"
    "2027-2028/departments/department/?department=W"
)
FEE_URL = (
    "https://www.aber.ac.uk/en/study-with-us/"
    "fees/postgrad/fees/"
)

EXPECTED_FEE = Decimal("23820.00")


def text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def download(session, url):
    response = session.get(url, timeout=(20, 60))
    response.raise_for_status()

    if response.url.rstrip("/") != url.rstrip("/"):
        raise ValueError(f"Unexpected redirect: {response.url}")

    soup = BeautifulSoup(response.content, "html.parser")

    return soup, {
        "source_url": response.url,
        "sha256": hashlib.sha256(response.content).hexdigest(),
    }


def verify_sources(session):
    course, course_evidence = download(session, COURSE_URL)
    department, department_evidence = download(session, DEPARTMENT_URL)
    fees, fee_evidence = download(session, FEE_URL)

    heading = course.find("h1")
    if heading is None or text(heading) != "Behaviour Change":
        raise ValueError("Course page title does not match.")

    course_text = text(course)

    if not re.search(r"\bC801-MSC\b", course_text, re.I):
        raise ValueError("C801 MSc course identifier not found.")

    if not re.search(
        r"\bSeptember\s+start\s+2027\b",
        course_text,
        re.I,
    ):
        raise ValueError("September 2027 intake is not confirmed.")

    if not re.search(
        r"\bCourse duration\s+1\s+year\b",
        course_text,
        re.I,
    ):
        raise ValueError("One-year duration is not confirmed.")

    department_heading = department.find("h1")
    if (
        department_heading is None
        or text(department_heading) != "Psychology 2027/2028"
    ):
        raise ValueError("Wrong departmental academic year or subject.")

    department_matches = [
        link
        for link in department.find_all("a", href=True)
        if re.search(
            r"\bMSC\s+Behaviour Change\s+\[C801\]",
            text(link),
            re.I,
        )
    ]

    if len(department_matches) != 1:
        raise ValueError(
            "A unique C801 MSc entry under Psychology is required."
        )

    # Limit extraction to September 2027 international taught-master's fees.
    selected_nodes = []
    current_year = False
    selected_section = False

    for node in fees.find_all(["h2", "h3", "table"]):
        if node.name == "h2":
            current_year = (
                text(node).casefold()
                == "tuition fees for september 2027"
            )
            selected_section = False

        elif node.name == "h3":
            label = text(node).casefold()
            selected_section = (
                current_year
                and "taught" in label
                and "international" in label
            )

        elif current_year and selected_section:
            selected_nodes.append(node)

    amounts = []
    classification_found = False

    for table in selected_nodes:
        for row in table.find_all("tr"):
            cells = [
                text(cell)
                for cell in row.find_all(["th", "td"], recursive=False)
            ]
            if len(cells) < 2:
                continue

            if cells[0].casefold() != "science courses":
                continue

            if re.search(r"\bPsychology\b", cells[1], re.I):
                classification_found = True

            match = re.fullmatch(
                r"£\s*([\d,]+(?:\.\d{2})?)",
                cells[1],
            )
            if match:
                amounts.append(
                    Decimal(match.group(1).replace(",", "")).quantize(
                        Decimal("0.01")
                    )
                )

    if amounts != [EXPECTED_FEE]:
        raise ValueError(
            f"Expected one science fee of {EXPECTED_FEE}; found {amounts}."
        )

    if not classification_found:
        raise ValueError(
            "Psychology is not explicitly classified under science fees."
        )

    return {
        "course": course_evidence,
        "department": department_evidence,
        "fee_schedule": fee_evidence,
        "method": "official_department_and_published_subject_band",
        "classification": "Psychology",
        "fee_band": "Sciences",
        "academic_year": YEAR,
        "annual_tuition": str(EXPECTED_FEE),
    }


def source_values(source):
    if source is None:
        return None

    return {
        "academic_year": source.academic_year,
        "source_url": source.source_url,
        "source_course_name": source.source_course_name,
        "source_course_code": source.source_course_code,
        "fee_basis": source.fee_basis,
        "raw_fees": copy.deepcopy(source.raw_fees),
    }


class Command(BaseCommand):
    help = (
        "Preview or import Behaviour Change international tuition "
        "using its official Psychology classification."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            with requests.Session() as session:
                session.headers["User-Agent"] = (
                    "TuitionCompareSourceInspector/1.0"
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
                raise CommandError("Active course was not found.") from exc

            if (
                course.title != "MSc Behaviour Change"
                or course.level != "PG"
                or course.duration != "1 Year"
                or course.start_month != "September"
                or course.start_year != 2027
                or course.course_url.rstrip("/") != COURSE_URL.rstrip("/")
            ):
                raise CommandError(
                    "Database course does not match the corrected route."
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
            source = sources.first()

            if source is not None and not isinstance(source.raw_fees, dict):
                raise CommandError("Source metadata requires review.")

            before = {
                "tuition_fee": str(
                    Decimal(str(tuition.tuition_fee)).quantize(
                        Decimal("0.01")
                    )
                ),
                "currency": tuition.currency,
                "source": source_values(source),
            }

            raw = copy.deepcopy(source.raw_fees if source else {})

            raw.update({
                "verified": True,
                "fee_verification_status": "verified",
                "academic_year": YEAR,
                "source_fee_category": "International",
                "currency": "GBP",
                "annual_tuition": str(EXPECTED_FEE),
                "fee_band": "Sciences",
                "programme_fee_group": "masters",
                "course_kind": "postgraduate_taught",
                "course_source_url": COURSE_URL,
                "study_year": 1,
                "fee_method": evidence["method"],
                "fee_classification_evidence": evidence,
                "fee_review": {
                    "status": "resolved",
                    "reason": (
                        "The 2027/28 Psychology departmental register "
                        "lists C801 MSc Behaviour Change. The international "
                        "taught-master's fee schedule classifies Psychology "
                        "as Science."
                    ),
                },
                "scholarship_status": "pending_separate_reconciliation",
                "deposit_status": "offer_letter_amount_required",
                "fee_categories": [{
                    "source_fee_category": "International",
                    "academic_year": YEAR,
                    "currency": "GBP",
                    "annual_tuition": str(EXPECTED_FEE),
                    "raw_annual_fee": "£23,820",
                    "annotation": (
                        "Full-time taught master's fee, mapped through "
                        "the official Psychology departmental register."
                    ),
                    "footnote_marker": "",
                    "amount_requires_review": False,
                }],
            })

            display_notes = raw.get("display_notes", {})
            if not isinstance(display_notes, dict):
                display_notes = {}

            display_notes.pop("fee", None)
            display_notes["scholarship"] = (
                "Scholarship applicability requires separate reconciliation."
            )
            display_notes["deposit"] = (
                "Confirm the deposit amount in the individual offer letter."
            )
            raw["display_notes"] = display_notes

            after = {
                "tuition_fee": str(EXPECTED_FEE),
                "currency": "GBP",
                "source": {
                    "academic_year": YEAR,
                    "source_url": FEE_URL,
                    "source_course_name": "Behaviour Change",
                    "source_course_code": "C801",
                    "fee_basis": "annual_tuition",
                    "raw_fees": raw,
                },
            }

            changed = before != after
            now = datetime.now(timezone.utc)
            folder = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
            folder.mkdir(parents=True, exist_ok=True)

            report_path = folder / (
                "behaviour-change-fee-"
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
                        "evidence": evidence,
                        "before": before,
                        "after": after,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.stdout.write(
                f"{COURSE_ID}: GBP {before['tuition_fee']} "
                f"-> {EXPECTED_FEE}; September 2027 / {YEAR} — "
                f"{'update' if changed else 'unchanged'}"
            )
            self.stdout.write(f"Proposed changes: {int(changed)}")
            self.stdout.write(f"Report: {report_path}")

            if not options["apply"]:
                self.stdout.write(
                    "Preview only. No database records changed."
                )
                return

            if changed:
                tuition.tuition_fee = EXPECTED_FEE
                tuition.save(update_fields=["tuition_fee"])

                TuitionSource.objects.update_or_create(
                    tuition=tuition,
                    defaults=after["source"],
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
                "Verified Behaviour Change tuition and source saved."
                if changed
                else "Already current. No database updates needed."
            )
        )
        self.stdout.write(
            "Scholarship deduction and stored deposit unchanged."
        )