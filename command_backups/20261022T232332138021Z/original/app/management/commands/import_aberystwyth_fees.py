import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone as django_timezone

from app.models import (
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)


FEE_URL = (
    "https://www.aber.ac.uk/en/study-with-us/"
    "international/fees-scholarships/"
)

# Reviewed course identities and department associations.
# The amounts are extracted from the collected official tables.
REVIEWED_COURSES = {
    "C_UK002_01": ("Accounting and Finance", "bachelors", "/en/abs"),
    "C_UK002_04": ("Business and Management", "bachelors", "/en/abs"),
    "C_UK002_15": ("Art History", "bachelors", "/en/art"),
    "C_UK002_16": ("Economics", "bachelors", "/en/abs"),
    "C_UK002_17": ("Education", "bachelors", "/en/education"),
    "C_UK002_18": ("Fine Art", "bachelors", "/en/art"),
    "C_UK002_44": ("Marketing", "bachelors", "/en/abs"),
    "C_UK002_53": (
        "Accounting and Finance / Business and Management",
        "bachelors",
        "/en/abs",
    ),
    "C_UK002_76": ("Business Finance", "bachelors", "/en/abs"),
    "C_UK002_77": ("Business Economics", "bachelors", "/en/abs"),
    "C_UK002_78": (
        "Business and Climate Change",
        "bachelors",
        "/en/abs",
    ),
    "C_UK002_79": (
        "Economics and Climate Change",
        "bachelors",
        "/en/abs",
    ),
    "C_UK002_87": (
        "Fine Art with Art History",
        "bachelors",
        "/en/art",
    ),
    "C_UK002_49": (
        "Archives and Records Management",
        "masters",
        "/en/dis",
    ),
    "C_UK002_62": ("Management and Finance", "masters", "/en/abs"),
    "C_UK002_63": (
        "International Business and Marketing",
        "masters",
        "/en/abs",
    ),
    "C_UK002_75": (
        "Information and Library Studies",
        "masters",
        "/en/dis",
    ),
    "C_UK002_89": ("Finance", "masters", "/en/abs"),
    "C_UK002_90": (
        "International Finance and Banking",
        "masters",
        "/en/abs",
    ),
}


def serialisable(value):
    return json.loads(
        json.dumps(value, cls=DjangoJSONEncoder)
    )


def read_json(path):
    try:
        return json.loads(
            path.read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError) as exc:
        raise CommandError(
            f"Cannot read {path}: {exc}"
        ) from exc


def latest_report(directory, filename):
    paths = list(directory.glob(f"*/{filename}"))

    if not paths:
        raise CommandError(
            f"No {filename} found under {directory}."
        )

    return max(paths, key=lambda path: path.parent.name)


def validate_age(report):
    try:
        generated = datetime.fromisoformat(report["generated_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CommandError(
            "Source report has no valid collection timestamp."
        ) from exc

    if generated.tzinfo is None:
        raise CommandError("Collection timestamp has no timezone.")

    age = datetime.now(timezone.utc) - generated

    if age < timedelta(minutes=-5) or age > timedelta(days=7):
        raise CommandError(
            "Source evidence is older than seven days or has "
            "a future timestamp. Refresh the source collectors."
        )


def money(raw):
    text = str(raw).strip()

    if not text.startswith("£"):
        raise CommandError(
            f"Expected a single GBP fee, received {raw!r}."
        )

    try:
        amount = Decimal(
            text[1:].strip().replace(",", "")
        )
    except InvalidOperation as exc:
        raise CommandError(
            f"Invalid fee: {raw!r}"
        ) from exc

    if not amount.is_finite() or amount <= 0:
        raise CommandError(f"Invalid fee: {raw!r}")

    return amount.quantize(Decimal("0.01"))


def extract_reviewed_prices(source_report):
    sources = source_report.get("sources", {})
    fees = sources.get("international_fees", {})
    scholarship = sources.get("global_citizens_scholarship", {})

    if (
        fees.get("status") != "collected"
        or scholarship.get("status") != "collected"
        or fees.get("response_url", "").rstrip("/")
        != FEE_URL.rstrip("/")
    ):
        raise CommandError("Required official sources are missing.")

    # This initial reviewed adapter supports 2027/28 only.
    # A new year must first be checked against its source tables.
    if (
        "Tuition Fees for International Students 2027/2028"
        not in fees.get("text", "")
    ):
        raise CommandError(
            "The reviewed 2027/28 fee section was not found."
        )

    matching = []

    for table in fees.get("tables", []):
        rows = table.get("rows", [])

        if not rows or len(rows[0]) < 3:
            continue

        if (
            rows[0][1].casefold() != "humanities"
            or rows[0][2].casefold() != "sciences"
        ):
            continue

        # Distinguish the reviewed year's table from 2026/27.
        bachelor_rows = [
            row for row in rows[1:]
            if row
            and row[0].startswith("Bachelors Programme")
        ]

        if (
            len(bachelor_rows) == 1
            and len(bachelor_rows[0]) >= 3
            and money(bachelor_rows[0][1]) == Decimal("20400.00")
        ):
            matching.append(rows)

    if len(matching) != 1:
        raise CommandError(
            "Cannot uniquely identify the reviewed 2027/28 table."
        )

    rows = matching[0]
    prices = {}

    for group, prefix in (
        ("bachelors", "Bachelors Programme"),
        ("masters", "Masters Programme"),
    ):
        selected = [
            row for row in rows[1:]
            if row and row[0].startswith(prefix)
        ]

        if len(selected) != 1 or len(selected[0]) < 3:
            raise CommandError(
                f"Ambiguous humanities fee for {group}."
            )

        prices[group] = money(selected[0][1])

    # Cross-check humanities amounts against scholarship tables.
    scholarship_tables = []

    for table in scholarship.get("tables", []):
        rows = table.get("rows", [])

        if (
            rows
            and any("2027/28" in cell for cell in rows[0])
        ):
            scholarship_tables.append(rows)

    if len(scholarship_tables) < 2:
        raise CommandError(
            "Scholarship-year comparison tables are incomplete."
        )

    for group, index in (("bachelors", 0), ("masters", 1)):
        humanities_rows = [
            row for row in scholarship_tables[index][1:]
            if row and row[0].strip().casefold() == "humanities"
        ]

        if (
            len(humanities_rows) != 1
            or len(humanities_rows[0]) < 2
            or money(humanities_rows[0][1]) != prices[group]
        ):
            raise CommandError(
                f"Official sources disagree about {group} fees."
            )

    # Pin this first reviewed import to inspected amounts.
    if prices != {
        "bachelors": Decimal("20400.00"),
        "masters": Decimal("22115.00"),
    }:
        raise CommandError(
            "Reviewed fee amounts changed; inspect before importing."
        )

    return prices


def has_department_link(page, department_path):
    for link in page.get("links", []):
        parsed = urlsplit(link.get("url", ""))

        if (
            parsed.scheme == "https"
            and parsed.hostname in ("www.aber.ac.uk", "aber.ac.uk")
            and parsed.path.rstrip("/") == department_path.rstrip("/")
        ):
            return True

    return False


def snapshot(course, tuition, source):
    return serialisable({
        "course": {
            "title": course.title,
            "duration": course.duration,
            "start_month": course.start_month,
            "start_year": course.start_year,
            "course_url": course.course_url,
        },
        "tuition": {
            "tuition_fee": tuition.tuition_fee,
            "currency": tuition.currency,
        },
        "source": (
            {
                "academic_year": source.academic_year,
                "source_url": source.source_url,
                "source_course_name": source.source_course_name,
                "source_course_code": source.source_course_code,
                "fee_basis": source.fee_basis,
                "raw_fees": source.raw_fees,
            }
            if source
            else None
        ),
    })


class Command(BaseCommand):
    help = (
        "Preview/apply a reviewed first batch of Aberystwyth "
        "September 2027 international tuition and intake updates."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        directory = root / "logs" / "aberystwyth"

        source_path = latest_report(
            directory,
            "source-review.json",
        )
        course_path = latest_report(
            directory,
            "course-evidence.json",
        )

        source_report = read_json(source_path)
        course_report = read_json(course_path)

        for report in (source_report, course_report):
            if report.get("university_id") != "UK002":
                raise CommandError(
                    "Evidence belongs to another university."
                )

            validate_age(report)

        prices = extract_reviewed_prices(source_report)

        pages = {}

        for page in course_report.get("pages", []):
            course_id = page.get("course_id")

            if course_id in pages:
                raise CommandError(
                    f"Duplicate course evidence: {course_id}"
                )

            pages[course_id] = page

        proposals = []
        blocked = []
        now = django_timezone.now()

        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        report_path = directory / f"import-{stamp}.json"

        with transaction.atomic():
            try:
                university = (
                    University.objects.select_for_update()
                    .get(pk="UK002", country="UK")
                )
            except University.DoesNotExist as exc:
                raise CommandError(
                    "Aberystwyth University UK002 was not found."
                ) from exc

            if university.name.casefold() != "aberystwyth university":
                raise CommandError("University identity mismatch.")

            for course_id, reviewed in REVIEWED_COURSES.items():
                expected_title, group, department = reviewed
                page = pages.get(course_id)

                reason = None

                if not page or page.get("status") != "collected":
                    reason = "Collected course-page evidence is missing."

                elif page.get("title") != expected_title:
                    reason = "Source course title changed."

                elif not has_department_link(page, department):
                    reason = "Reviewed department association is missing."

                elif page.get("advertised_intakes") != [
                    {"month": "September", "year": 2027}
                ]:
                    reason = "The reviewed September 2027 intake changed."

                if reason:
                    blocked.append({
                        "course_id": course_id,
                        "reason": reason,
                    })
                    continue

                try:
                    course = (
                        Course.objects.select_for_update()
                        .get(
                            pk=course_id,
                            university=university,
                            is_active=True,
                        )
                    )
                except Course.DoesNotExist:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Active database course not found.",
                    })
                    continue

                if (
                    course.title != page["stored_title"]
                    or course.duration != page["stored_duration"]
                    or course.level != page["stored_level"]
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": (
                            "Database identity changed since collection."
                        ),
                    })
                    continue

                if (
                    (group == "bachelors" and course.level != "UG")
                    or (group == "masters" and course.level not in ("PG", "PGT"))
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Course level does not match reviewed fee group.",
                    })
                    continue

                tuitions = list(
                    Tuition.objects.select_for_update().filter(
                        course=course,
                        residency="INTL",
                    )
                )

                if len(tuitions) != 1 or tuitions[0].currency != "GBP":
                    blocked.append({
                        "course_id": course_id,
                        "reason": "A unique GBP international tuition is required.",
                    })
                    continue

                tuition = tuitions[0]

                source = TuitionSource.objects.filter(
                    tuition=tuition
                ).first()

                before = snapshot(course, tuition, source)
                fee = prices[group]

                raw_fees = {
                    "verified": True,
                    "academic_year": "2027/28",
                    "source_fee_category": "International",
                    "fee_band": "Humanities",
                    "programme_fee_group": group,
                    "course_kind": (
                        "undergraduate"
                        if group == "bachelors"
                        else "postgraduate_taught"
                    ),
                    "course_source_url": page["response_url"],
                    "annual_tuition": str(fee),
                    "currency": "GBP",
                    "fee_method": "reviewed_course_and_published_band",
                    "scholarship_status": "pending_separate_reconciliation",
                    "deposit_status": "offer_letter_amount_required",
                    "fee_categories": [
                        {
                            "source_fee_category": "International",
                            "academic_year": "2027/28",
                            "currency": "GBP",
                            "annual_tuition": str(fee),
                            "raw_annual_fee": f"£{fee:,.2f}",
                            "footnote_marker": "",
                            "annotation": "",
                            "amount_requires_review": False,
                        }
                    ],
                }

                after = {
                    "course": {
                        **before["course"],
                        "start_month": "September",
                        "start_year": 2027,
                        "course_url": page["response_url"],
                    },
                    "tuition": {
                        "tuition_fee": str(fee),
                        "currency": "GBP",
                    },
                    "source": {
                        "academic_year": "2027/28",
                        "source_url": FEE_URL,
                        "source_course_name": expected_title,
                        "source_course_code": "",
                        "fee_basis": "annual_tuition",
                        "raw_fees": raw_fees,
                    },
                }

                # Compare money numerically rather than its string formatting.
                normal_before = json.loads(json.dumps(before))
                normal_before["tuition"]["tuition_fee"] = str(
                    Decimal(str(tuition.tuition_fee)).quantize(
                        Decimal("0.01")
                    )
                )

                changed = normal_before != after

                proposals.append({
                    "course_id": course_id,
                    "changed": changed,
                    "before": before,
                    "after": after,
                })

                self.stdout.write(
                    f"{course_id}: GBP {tuition.tuition_fee} "
                    f"-> {fee}; September 2027 "
                    f"— {'update' if changed else 'unchanged'}"
                )

            report = {
                "generated_at": now.isoformat(),
                "mode": "apply" if options["apply"] else "preview",
                "university_id": "UK002",
                "academic_year": "2027/28",
                "source_report": str(source_path),
                "course_report": str(course_path),
                "proposals": proposals,
                "blocked": blocked,
                "outside_this_batch": [
                    course_id
                    for course_id in Course.objects.filter(
                        university=university,
                        is_active=True,
                    ).values_list("course_id", flat=True)
                    if course_id not in REVIEWED_COURSES
                ],
            }

            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    report,
                    cls=DjangoJSONEncoder,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            if options["apply"]:
                for proposal in proposals:
                    if not proposal["changed"]:
                        continue

                    course_id = proposal["course_id"]
                    after = proposal["after"]

                    course = Course.objects.get(pk=course_id)
                    tuition = Tuition.objects.get(
                        course=course,
                        residency="INTL",
                    )

                    course.start_month = after["course"]["start_month"]
                    course.start_year = after["course"]["start_year"]
                    course.course_url = after["course"]["course_url"]

                    course.save(update_fields=[
                        "start_month",
                        "start_year",
                        "course_url",
                        "updated_at",
                    ])

                    tuition.tuition_fee = Decimal(
                        after["tuition"]["tuition_fee"]
                    )
                    tuition.save(update_fields=["tuition_fee"])

                    TuitionSource.objects.update_or_create(
                        tuition=tuition,
                        defaults=after["source"],
                    )

                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=course_id,
                        residency="INTL",
                        before=proposal["before"],
                        after=after,
                    )

        self.stdout.write(
            f"\nMatched batch records: {len(proposals)}"
        )
        self.stdout.write(
            "Proposed changes: "
            f"{sum(p['changed'] for p in proposals)}"
        )
        self.stdout.write(f"Blocked in batch: {len(blocked)}")
        self.stdout.write(f"Report: {report_path}")

        if options["apply"]:
            self.stdout.write(
                "Applied reviewed intake, course URL, international "
                "tuition and source metadata updates."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )

        self.stdout.write(
            "Scholarships, EU tuition and stored deposits were unchanged."
        )