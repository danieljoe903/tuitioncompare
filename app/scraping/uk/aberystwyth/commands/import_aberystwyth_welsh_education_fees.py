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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


FEE_URL = (
    "https://www.aber.ac.uk/en/study-with-us/international/"
    "fees-scholarships/"
)

COURSES = {
    "C_ABE002_RES_09": {
        "old_title": "MPhil (Research) Welsh / Cymraeg",
        "title": "MPhil Welsh",
        "official_title": "Welsh",
        "code": "Q5562",
        "award": "MPHL",
        "duration": "1 Year",
        "month": "September",
        "year": 2027,
        "academic_year": "2027/28",
        "amount": "20270.00",
        "url": "https://courses.aber.ac.uk/postgraduate/welsh-mphil/",
    },
    "C_UK002_68": {
        "old_title": "PhD Education / Childhood Studies",
        "title": "PhD Education",
        "official_title": "Education",
        "code": "X3010",
        "award": "PHD",
        "duration": "3 Years",
        "month": "January",
        "year": 2027,
        "academic_year": "2026/27",
        "amount": "19070.00",
        "url": (
            "https://courses.aber.ac.uk/postgraduate/"
            "research-education/"
        ),
    },
}

COURSE_FIELDS = (
    "title",
    "level",
    "duration",
    "start_month",
    "start_year",
    "course_url",
)


def text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def download(session, url):
    response = session.get(url, timeout=(30, 60))
    response.raise_for_status()

    if response.url.rstrip("/") != url.rstrip("/"):
        raise ValueError(f"Unexpected redirect: {response.url}")

    return BeautifulSoup(response.content, "html.parser"), {
        "source_url": response.url,
        "sha256": hashlib.sha256(response.content).hexdigest(),
    }


def amount(value):
    match = re.fullmatch(
        r"£\s*([\d,]+(?:\.\d{2})?)",
        value.strip(),
    )
    if not match:
        raise ValueError(f"Invalid published amount: {value}")

    return Decimal(match.group(1).replace(",", "")).quantize(
        Decimal("0.01")
    )


def verify_fee_table(soup, academic_year, expected):
    selected_year = None
    matches = []
    subjects_confirmed = False

    for node in soup.find_all(["h2", "h3", "table"]):
        if node.name in ("h2", "h3"):
            label = text(node)
            match = re.search(
                r"Tuition Fees for International Students\s+"
                r"(20\d{2})/(20\d{2})",
                label,
                re.I,
            )
            if match:
                selected_year = (
                    f"{match.group(1)}/{match.group(2)[-2:]}"
                )
            continue

        rows = []
        for row in node.find_all("tr"):
            cells = [
                text(cell)
                for cell in row.find_all(
                    ["th", "td"],
                    recursive=False,
                )
            ]
            if cells:
                rows.append(cells)

        for cells in rows:
            if (
                len(cells) >= 2
                and cells[0].casefold() == "humanities"
                and "education" in cells[1].casefold()
                and "welsh" in cells[1].casefold()
            ):
                subjects_confirmed = True

        if selected_year != academic_year:
            continue

        if not rows or len(rows[0]) < 3:
            continue

        if (
            rows[0][1].casefold() != "humanities"
            or rows[0][2].casefold() != "sciences"
        ):
            continue

        for cells in rows[1:]:
            if (
                len(cells) >= 3
                and "research programmes" in cells[0].casefold()
                and "mphil" in cells[0].casefold()
                and "phd" in cells[0].casefold()
            ):
                matches.append(amount(cells[1]))

    if matches != [Decimal(expected)]:
        raise ValueError(
            f"{academic_year}: expected one Humanities research fee "
            f"of {expected}; found {matches}."
        )

    if not subjects_confirmed:
        raise ValueError(
            "Education and Welsh Humanities classification not found."
        )


def verify_course(soup, config):
    heading = soup.find("h1")
    if heading is None or text(heading) != config["official_title"]:
        raise ValueError("Official course title does not match.")

    content = text(soup)

    if not re.search(
        rf"\b{re.escape(config['code'])}-{config['award']}\b",
        content,
        re.I,
    ):
        raise ValueError("Source course code/award does not match.")

    years = config["duration"].split()[0]
    if not re.search(
        rf"\bCourse duration\s+{years}\s+years?\b",
        content,
        re.I,
    ):
        raise ValueError("Full-time course duration does not match.")

    intake = (
        rf"\b{config['month']}\s+start\s+{config['year']}\b"
    )
    if not re.search(intake, content, re.I):
        raise ValueError("Reviewed intake is not advertised.")

    if config["code"] == "X3010":
        if "childhood studies" not in content.casefold():
            raise ValueError(
                "Education page does not confirm Childhood Studies "
                "as a research area."
            )


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
        "source": None if source is None else {
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
        "Preview or import reviewed Welsh MPhil and Education PhD "
        "course corrections and international research fees."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        evidence = {}

        try:
            with requests.Session() as session:
                retry = Retry(
                    total=3,
                    connect=3,
                    read=3,
                    status=3,
                    backoff_factor=1,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET"],
                    respect_retry_after_header=True
                )
                session.mount("https://",  HTTPAdapter(max_retries=retry))
                session.headers["User-Agent"] = (
                    "TuitionCompareSourceInspector/1.0"
                )

                fee_soup, fee_evidence = download(session, FEE_URL)

                for course_id, config in COURSES.items():
                    soup, course_evidence = download(
                        session,
                        config["url"],
                    )
                    verify_course(soup, config)
                    verify_fee_table(
                        fee_soup,
                        config["academic_year"],
                        config["amount"],
                    )
                    evidence[course_id] = {
                        "course": course_evidence,
                        "fee_schedule": fee_evidence,
                    }

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

            for course_id, config in COURSES.items():
                courses = Course.objects.filter(
                    university_id="UK002",
                    course_id=course_id,
                    is_active=True,
                )
                if options["apply"]:
                    courses = courses.select_for_update()

                try:
                    course = courses.get()
                except Course.DoesNotExist as exc:
                    raise CommandError(
                        f"{course_id}: active course not found."
                    ) from exc

                if course.title not in {
                    config["old_title"],
                    config["title"],
                }:
                    raise CommandError(
                        f"{course_id}: unexpected stored title "
                        f"{course.title!r}; no replacement applied."
                    )

                if Course.objects.filter(
                    university_id="UK002",
                    is_active=True,
                    course_url=config["url"],
                ).exclude(course_id=course_id).exists():
                    raise CommandError(
                        f"{course_id}: another active record already "
                        "uses this course URL. Review duplication first."
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
                        f"{course_id}: unique GBP international "
                        "tuition required."
                    )

                tuition = tuitions[0]

                sources = TuitionSource.objects.filter(
                    tuition=tuition
                )
                if options["apply"]:
                    sources = sources.select_for_update()
                source = sources.first()

                if source and not isinstance(source.raw_fees, dict):
                    raise CommandError(
                        f"{course_id}: invalid source metadata."
                    )

                before = snapshot(course, tuition, source)
                after = copy.deepcopy(before)

                after["course"].update({
                    "title": config["title"],
                    "level": "PG",
                    "duration": config["duration"],
                    "start_month": config["month"],
                    "start_year": config["year"],
                    "course_url": config["url"],
                })

                raw = copy.deepcopy(
                    source.raw_fees if source else {}
                )
                raw.update({
                    "verified": True,
                    "fee_verification_status": "verified",
                    "academic_year": config["academic_year"],
                    "source_fee_category": "International",
                    "annual_tuition": config["amount"],
                    "currency": "GBP",
                    "fee_band": "Humanities",
                    "programme_fee_group": "research",
                    "course_kind": "postgraduate_research",
                    "research_award": (
                        "MPHIL"
                        if config["award"] == "MPHL"
                        else "PHD"
                    ),
                    "course_source_url": config["url"],
                    "study_year": 1,
                    "fee_method": (
                        "reviewed_course_and_published_subject_band"
                    ),
                    "course_identity": {
                        "status": "confirmed_current_catalogue_match",
                        "official_title": config["official_title"],
                        "official_code": config["code"],
                        "official_award": config["award"],
                        "source_url": config["url"],
                    },
                    "fee_review": {
                        "status": "resolved",
                        "reason": (
                            "Official research course mapped to the "
                            "Humanities fee for its advertised intake."
                        ),
                    },
                    "fee_evidence": evidence[course_id],
                    "annual_fee_increase_possible": True,
                    "scholarship_status": (
                        "pending_separate_reconciliation"
                    ),
                    "deposit_status": "offer_letter_amount_required",
                    "fee_categories": [{
                        "source_fee_category": "International",
                        "academic_year": config["academic_year"],
                        "currency": "GBP",
                        "annual_tuition": config["amount"],
                        "raw_annual_fee": (
                            f"£{Decimal(config['amount']):,.0f}"
                        ),
                        "annotation": (
                            "Annual full-time research tuition. "
                            "Fees may increase in subsequent years."
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
                    "Scholarship applicability has not yet been confirmed."
                )
                notes["deposit"] = (
                    "Deposit amount is specified in your individual "
                    "offer letter."
                )
                raw["display_notes"] = notes

                after["tuition"]["tuition_fee"] = config["amount"]
                after["source"] = {
                    "academic_year": config["academic_year"],
                    "source_url": FEE_URL,
                    "source_course_name": config["official_title"],
                    "source_course_code": config["code"],
                    "fee_basis": "annual_tuition",
                    "raw_fees": raw,
                }

                plans.append({
                    "course_id": course_id,
                    "before": before,
                    "after": after,
                    "changed": before != after,
                    "course": course,
                    "tuition": tuition,
                })

            folder = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
            folder.mkdir(parents=True, exist_ok=True)
            now = datetime.now(timezone.utc)
            report_path = folder / (
                "welsh-education-fees-"
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
                    "records": [
                        {
                            key: plan[key]
                            for key in (
                                "course_id",
                                "changed",
                                "before",
                                "after",
                            )
                        }
                        for plan in plans
                    ],
                }, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            for plan in plans:
                config = COURSES[plan["course_id"]]
                self.stdout.write(
                    f"{plan['course_id']}: "
                    f"GBP {plan['before']['tuition']['tuition_fee']} "
                    f"-> {config['amount']}; "
                    f"{config['month']} {config['year']} / "
                    f"{config['academic_year']} — "
                    f"{'update' if plan['changed'] else 'unchanged'}"
                )

                for field in COURSE_FIELDS:
                    old = plan["before"]["course"][field]
                    new = plan["after"]["course"][field]
                    if old != new:
                        self.stdout.write(
                            f"  {field}: {old} -> {new}"
                        )

            self.stdout.write(f"\nMatched records: {len(plans)}")
            self.stdout.write(
                "Proposed changes: "
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
                tuition.tuition_fee = Decimal(
                    plan["after"]["tuition"]["tuition_fee"]
                )
                tuition.save(update_fields=["tuition_fee"])

                TuitionSource.objects.update_or_create(
                    tuition=tuition,
                    defaults=plan["after"]["source"],
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
                "Reviewed course corrections and research fees saved."
            )
        )
        self.stdout.write(
            "Scholarships require separate reconciliation. "
            "Deposit amount is specified in the individual offer letter."
        )