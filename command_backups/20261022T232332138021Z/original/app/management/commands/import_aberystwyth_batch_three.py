import json
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)

from .import_aberystwyth_fees import (
    FEE_URL,
    latest_report,
    money,
    read_json,
    snapshot,
    validate_age,
)


YEAR = "2027/28"

# Official title, code, award, database level, fee group,
# duration in years, official course path.
REVIEWED = {
    "C_ABE002_01": (
        "Business and Management (Top-up)",
        "N12T",
        "BSC",
        "TOPUP",
        "ug_humanities",
        1,
        "/undergraduate/N12T-business-topup",
    ),
    "C_UK002_23": (
        "Business Administration",
        "N1834",
        "MBA",
        "PG",
        "mba",
        1,
        "/postgraduate/business-administration",
    ),
    "C_UK002_45": (
        "Global Supply Chain Management",
        "N1837",
        "MBA",
        "PG",
        "mba",
        1,
        "/postgraduate/global-management",
    ),
    "C_UK002_54": (
        "Computer Science / Mathematics",
        "GG14",
        "BSC",
        "UG",
        "ug_sciences",
        3,
        "/undergraduate/computer-science-and-mathematics-degree",
    ),
}

EXPECTED = {
    "ug_humanities": Decimal("20400.00"),
    "ug_sciences": Decimal("23255.00"),
    "mba": Decimal("23310.00"),
}


def prices_from_report(report):
    fees = report.get("sources", {}).get("international_fees", {})

    if (
        fees.get("status") != "collected"
        or fees.get("response_url", "").rstrip("/")
        != FEE_URL.rstrip("/")
        or "2027/2028" not in fees.get("text", "")
    ):
        raise CommandError("Reviewed 2027/28 fee evidence is missing.")

    matches = []

    for table in fees.get("tables", []):
        rows = table.get("rows", [])

        if not rows or len(rows[0]) < 4:
            continue

        headers = [
            str(cell).strip().casefold()
            for cell in rows[0]
        ]

        if (
            headers[1:3] != ["humanities", "sciences"]
            or "business administration" not in headers[3]
        ):
            continue

        bachelors = [
            row for row in rows[1:]
            if row and str(row[0]).startswith("Bachelors Programme")
        ]
        masters = [
            row for row in rows[1:]
            if row and str(row[0]).startswith("Masters Programme")
        ]

        if (
            len(bachelors) != 1
            or len(masters) != 1
            or len(bachelors[0]) < 3
            or len(masters[0]) < 4
        ):
            continue

        values = {
            "ug_humanities": money(bachelors[0][1]),
            "ug_sciences": money(bachelors[0][2]),
            "mba": money(masters[0][3]),
        }

        if values == EXPECTED:
            matches.append(values)

    if len(matches) != 1:
        raise CommandError(
            "Cannot uniquely identify the reviewed tuition table. "
            "Inspect the official evidence before importing."
        )

    return matches[0]


def course_fields(course):
    return {
        "title": course.title,
        "duration": course.duration,
        "start_month": course.start_month,
        "start_year": course.start_year,
        "course_url": course.course_url,
        "level": course.level,
        "ucas_cao_code": course.ucas_cao_code,
    }


def before_snapshot(course, tuition, source):
    if tuition is None:
        return {
            "course": course_fields(course),
            "tuition": None,
            "source": None,
        }

    result = snapshot(course, tuition, source)
    result["course"] = course_fields(course)
    result["tuition"]["tuition_fee"] = str(
        Decimal(str(tuition.tuition_fee)).quantize(Decimal("0.01"))
    )
    return result


def validate_page(page, reviewed):
    title, code, award, level, group, years, path = reviewed

    if not page or page.get("status") != "collected":
        return "Collected course evidence is missing."

    if page.get("title") != title:
        return "Official title changed."

    parsed = urlsplit(page.get("source_url", ""))

    if (
        parsed.scheme != "https"
        or parsed.netloc != "courses.aber.ac.uk"
        or parsed.path.rstrip("/") != path
        or parsed.query
        or parsed.fragment
    ):
        return "Official course URL changed."

    content = page.get("text", "")

    if not re.search(
        rf"\b{re.escape(code)}-{re.escape(award)}\b",
        content,
        flags=re.IGNORECASE,
    ):
        return "Official course code or award is not confirmed."

    if not re.search(
        rf"Course duration\s+{years}\s+years?\b",
        content,
        flags=re.IGNORECASE,
    ):
        return "Official course duration changed."

    if page.get("advertised_intakes") != [
        {"month": "September", "year": 2027}
    ]:
        return "Reviewed September 2027 intake changed."

    return None


class Command(BaseCommand):
    help = (
        "Preview/apply four reviewed Aberystwyth courses, including "
        "creation of missing international tuition records."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        directory = Path(settings.BASE_DIR) / "logs" / "aberystwyth"

        source_path = latest_report(directory, "source-review.json")
        paths = list(
            directory.glob("next-batch-*/next-course-evidence.json")
        )

        if not paths:
            raise CommandError(
                "No next-course-evidence.json found inside a "
                "logs/aberystwyth/next-batch-* folder."
            )

        course_path = max(paths, key=lambda path: path.parent.name)
        source_report = read_json(source_path)
        course_report = read_json(course_path)

        for report in (source_report, course_report):
            if report.get("university_id") != "UK002":
                raise CommandError("University evidence mismatch.")
            validate_age(report)

        prices = prices_from_report(source_report)
        pages = {}

        for page in course_report.get("pages", []):
            course_id = page.get("course_id")
            if not course_id or course_id in pages:
                raise CommandError(
                    "Missing or duplicate evidence course ID."
                )
            pages[course_id] = page

        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        report_path = directory / f"batch-three-{stamp}.json"

        proposals = []
        blocked = []
        locked_records = {}

        with transaction.atomic():
            try:
                university = University.objects.select_for_update().get(
                    pk="UK002",
                    country="UK",
                )
            except University.DoesNotExist as exc:
                raise CommandError("Aberystwyth UK002 not found.") from exc

            if university.name.strip().casefold() != "aberystwyth university":
                raise CommandError("University identity mismatch.")

            for course_id, reviewed in REVIEWED.items():
                page = pages.get(course_id)
                reason = validate_page(page, reviewed)

                if reason:
                    blocked.append({
                        "course_id": course_id,
                        "reason": reason,
                    })
                    continue

                title, code, award, level, group, years, path = reviewed
                stored = page.get("stored", {})

                course = Course.objects.select_for_update().filter(
                    pk=course_id,
                    university=university,
                    is_active=True,
                ).first()

                if course is None:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Active database course not found.",
                    })
                    continue

                proposed_title = (
                    "BSc (Hons) Business and Management (Top-up)"
                    if course_id == "C_ABE002_01"
                    else stored.get("title")
                )

                if (
                    not proposed_title
                    or course.title not in (
                        stored.get("title"), proposed_title
                    )
                    or course.level != stored.get("level")
                    or course.level != level
                    or course.duration != stored.get("duration")
                    or course.ucas_cao_code not in ("", code)
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Database identity requires fresh review.",
                    })
                    continue

                tuitions = list(
                    Tuition.objects.select_for_update().filter(
                        course=course,
                        residency="INTL",
                    )
                )

                # Missing tuition is supported. Duplicates and wrong
                # currencies still require review.
                if len(tuitions) > 1:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Multiple international tuition records.",
                    })
                    continue

                tuition = tuitions[0] if tuitions else None

                if tuition is not None and tuition.currency != "GBP":
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Existing international currency is not GBP.",
                    })
                    continue

                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                    if tuition is not None
                    else None
                )

                before = before_snapshot(course, tuition, source)
                fee = prices[group]

                band = (
                    "MBA" if group == "mba"
                    else "Sciences" if group == "ug_sciences"
                    else "Humanities"
                )

                raw = {
                    "verified": True,
                    "academic_year": YEAR,
                    "source_fee_category": "International",
                    "fee_band": band,
                    "programme_fee_group": (
                        "masters" if group == "mba" else "bachelors"
                    ),
                    "course_kind": (
                        "postgraduate_taught"
                        if group == "mba" else "undergraduate"
                    ),
                    "course_source_url": page["source_url"],
                    "annual_tuition": str(fee),
                    "currency": "GBP",
                    "study_year": 1,
                    "fee_method": "reviewed_course_and_published_band",
                    "scholarship_status": "pending_separate_reconciliation",
                    "deposit_status": "offer_letter_amount_required",
                    "fee_categories": [{
                        "source_fee_category": "International",
                        "academic_year": YEAR,
                        "currency": "GBP",
                        "annual_tuition": str(fee),
                        "raw_annual_fee": f"£{fee:,.2f}",
                        "footnote_marker": "",
                        "annotation": "First study-year tuition.",
                        "amount_requires_review": False,
                    }],
                }

                after = {
                    "course": {
                        **before["course"],
                        "title": proposed_title,
                        "start_month": "September",
                        "start_year": 2027,
                        "course_url": page["source_url"],
                        "ucas_cao_code": (
                            code if level in ("UG", "TOPUP")
                            else course.ucas_cao_code
                        ),
                    },
                    "tuition": {
                        "tuition_fee": str(fee),
                        "currency": "GBP",
                    },
                    "source": {
                        "academic_year": YEAR,
                        "source_url": FEE_URL,
                        "source_course_name": title,
                        "source_course_code": code,
                        "fee_basis": "annual_tuition",
                        "raw_fees": raw,
                    },
                }

                creating = tuition is None
                changed = before != after

                proposal = {
                    "course_id": course_id,
                    "action": (
                        "create_tuition" if creating
                        else "update" if changed
                        else "unchanged"
                    ),
                    "changed": changed,
                    "before": before,
                    "after": after,
                }

                if creating:
                    proposal["new_tuition_defaults"] = {
                        "residency": "INTL",
                        "scholarship": "0.00",
                        "scholarship_source_id": None,
                        "tuition_deposit": None,
                    }

                proposals.append(proposal)
                locked_records[course_id] = (course, tuition)

                old_fee = (
                    str(tuition.tuition_fee)
                    if tuition is not None else "MISSING"
                )

                self.stdout.write(
                    f"{course_id}: GBP {old_fee} -> {fee}; "
                    f"September 2027 — {proposal['action']}"
                )

                if course.title != proposed_title:
                    self.stdout.write(
                        f"  Title: {course.title} -> {proposed_title}"
                    )

            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "university_id": "UK002",
                        "academic_year": YEAR,
                        "mode": "apply" if options["apply"] else "preview",
                        "source_report": str(source_path),
                        "course_report": str(course_path),
                        "proposals": proposals,
                        "blocked": blocked,
                        "held_candidates": [{
                            "course_id": "C_UK002_60",
                            "reason": (
                                "Law with Criminology versus Law and "
                                "Criminology: route equivalence unconfirmed."
                            ),
                        }],
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            # Validate the entire batch before any database writes.
            if options["apply"] and blocked:
                raise CommandError(
                    f"{len(blocked)} records need review. "
                    f"No batch updates applied. See {report_path}"
                )

            if options["apply"]:
                for proposal in proposals:
                    if not proposal["changed"]:
                        continue

                    course_id = proposal["course_id"]
                    course, tuition = locked_records[course_id]
                    after = proposal["after"]

                    fields = [
                        "title",
                        "start_month",
                        "start_year",
                        "course_url",
                        "ucas_cao_code",
                    ]

                    for field in fields:
                        setattr(course, field, after["course"][field])

                    course.save(update_fields=fields + ["updated_at"])

                    if tuition is None:
                        # Recheck before creation. The university/course
                        # locks serialize cooperating importer runs.
                        if Tuition.objects.filter(
                            course=course,
                            residency="INTL",
                        ).exists():
                            raise CommandError(
                                f"{course_id}: international tuition "
                                "appeared during this run. Retry preview."
                            )

                        tuition = Tuition.objects.create(
                            course=course,
                            residency="INTL",
                            tuition_fee=Decimal(
                                after["tuition"]["tuition_fee"]
                            ),
                            currency="GBP",
                            scholarship=Decimal("0.00"),
                            scholarship_source=None,
                            tuition_deposit=None,
                        )
                    else:
                        tuition.tuition_fee = Decimal(
                            after["tuition"]["tuition_fee"]
                        )
                        tuition.save(update_fields=["tuition_fee"])

                    TuitionSource.objects.update_or_create(
                        tuition=tuition,
                        defaults=after["source"],
                    )

                    audit_after = json.loads(json.dumps(after))
                    audit_after["tuition"].update({
                        "tuition_id": tuition.pk,
                        "residency": tuition.residency,
                        "scholarship": str(tuition.scholarship),
                        "scholarship_source_id": (
                            tuition.scholarship_source_id
                        ),
                        "tuition_deposit": (
                            str(tuition.tuition_deposit)
                            if tuition.tuition_deposit is not None
                            else None
                        ),
                    })

                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=course_id,
                        residency="INTL",
                        before=proposal["before"],
                        after=audit_after,
                    )

        creates = sum(
            proposal["action"] == "create_tuition"
            for proposal in proposals
        )
        changes = sum(
            proposal["changed"] for proposal in proposals
        )

        self.stdout.write(f"\nMatched records: {len(proposals)}")
        self.stdout.write(f"Missing tuition records to create: {creates}")
        self.stdout.write(f"Proposed changes: {changes}")
        self.stdout.write(f"Blocked in import batch: {len(blocked)}")
        self.stdout.write("Additional candidate held for review: 1")
        self.stdout.write(f"Report: {report_path}")

        self.stdout.write(
            "Applied course corrections, tuition records, "
            "source metadata and audits."
            if options["apply"]
            else "Preview only. No database records changed."
        )

        self.stdout.write(
            "Existing scholarships, deposits and EU tuition unchanged. "
            "New tuition records use zero stored discount and no deposit amount."
        )