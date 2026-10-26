import copy
import json
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    Course,
    Scholarship,
    ScholarshipRule,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)

from .import_aberystwyth_fees import (
    read_json,
    snapshot,
    validate_age,
)


COURSE_ID = "C_UK002_61"
YEAR = "2027/28"
COURSE_URL = (
    "https://courses.aber.ac.uk/postgraduate/"
    "software-engineering-integrated-industrial-year/"
)
FEE_URL = (
    "https://www.aber.ac.uk/en/study-with-us/"
    "fees/postgrad/fees/"
)
SCHOLARSHIP_URL = (
    "https://www.aber.ac.uk/en/study-with-us/international/"
    "scholarships/global-citizens-scholarship/"
)


class Command(BaseCommand):
    help = (
        "Preview/apply G498 teaching tuition, separate placement charge "
        "and the existing verified first-year science scholarship scope."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        directory = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
        paths = list(
            directory.glob(
                "remaining-review-*/remaining-course-review.json"
            )
        )

        if not paths:
            raise CommandError("Remaining-course review report not found.")

        evidence_path = max(paths, key=lambda path: path.parent.name)
        evidence = read_json(evidence_path)

        if evidence.get("university_id") != "UK002":
            raise CommandError("University evidence mismatch.")

        validate_age(evidence)

        records = [
            row for row in evidence.get("records", [])
            if row.get("course_id") == COURSE_ID
        ]

        if len(records) != 1:
            raise CommandError("Unique G498 database-course evidence required.")

        record = records[0]
        candidates = [
            candidate
            for candidate in record.get("candidates", [])
            if candidate.get("source_url", "").rstrip("/")
            == COURSE_URL.rstrip("/")
        ]

        if len(candidates) != 1:
            raise CommandError("Unique G498 official-page evidence required.")

        page = candidates[0].get("evidence", {})
        content = page.get("text", "")

        if (
            page.get("status") != "collected"
            or page.get("title")
            != "Advanced Computer Science (with integrated year in industry)"
            or "G498-MSC" not in content
            or not re.search(
                r"Course duration\s+2\s+years\b",
                content,
                flags=re.IGNORECASE,
            )
            or page.get("advertised_intakes")
            != [{"month": "September", "year": 2027}]
        ):
            raise CommandError("Reviewed G498 course identity changed.")

        policy = evidence.get("policies", {}).get("postgraduate_fees", {})

        if (
            policy.get("status") != "collected"
            or policy.get("source_url") != FEE_URL
            or "Tuition Fees for September 2027"
            not in policy.get("text", "")
        ):
            raise CommandError("Reviewed postgraduate fee evidence missing.")

        matching_rows = []

        for table in policy.get("tables", []):
            for row in table:
                if (
                    len(row) >= 2
                    and "(G498)" in row[0]
                    and " ".join(row[1].split())
                    == "£23,820 plus £1,850 for placement"
                ):
                    matching_rows.append(row)

        if len(matching_rows) != 1:
            raise CommandError(
                "The reviewed international G498 fee row changed "
                "or is ambiguous."
            )

        fee = Decimal("23820.00")
        placement_fee = Decimal("1850.00")
        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        output = directory / f"placement-import-{stamp}.json"

        with transaction.atomic():
            university = University.objects.select_for_update().get(
                pk="UK002",
                country="UK",
            )

            if university.name.strip().casefold() != "aberystwyth university":
                raise CommandError("University identity mismatch.")

            try:
                course = Course.objects.select_for_update().get(
                    pk=COURSE_ID,
                    university=university,
                    is_active=True,
                )
            except Course.DoesNotExist as exc:
                raise CommandError("Active G498 database course missing.") from exc

            stored = record.get("stored", {})

            if (
                course.title != stored.get("title")
                or course.duration != stored.get("duration")
                or course.level != "PG"
            ):
                raise CommandError("Database identity changed since collection.")

            records = list(
                Tuition.objects.select_for_update().filter(
                    course=course,
                    residency="INTL",
                )
            )

            if len(records) != 1 or records[0].currency != "GBP":
                raise CommandError(
                    "A unique GBP international tuition record is required."
                )

            tuition = records[0]
            source = (
                TuitionSource.objects.select_for_update()
                .filter(tuition=tuition)
                .first()
            )

            before = snapshot(course, tuition, source)
            before["course"]["work_placement"] = course.work_placement
            before["tuition"]["tuition_fee"] = str(
                Decimal(str(tuition.tuition_fee)).quantize(Decimal("0.01"))
            )

            annotation = (
                "First teaching-year tuition: £23,820. "
                "A separate £1,850 placement charge is published for "
                "the integrated industry route. The placement charge "
                "is not included in this first-year comparison."
            )

            after = {
                "course": {
                    **before["course"],
                    "start_month": "September",
                    "start_year": 2027,
                    "course_url": COURSE_URL,
                    "work_placement": True,
                },
                "tuition": {
                    "tuition_fee": str(fee),
                    "currency": "GBP",
                },
                "source": {
                    "academic_year": YEAR,
                    "source_url": FEE_URL,
                    "source_course_name": page["title"],
                    "source_course_code": "G498",
                    "fee_basis": "annual_tuition",
                    "raw_fees": {
                        "verified": True,
                        "academic_year": YEAR,
                        "source_fee_category": "International",
                        "fee_band": "Sciences",
                        "programme_fee_group": "masters",
                        "course_kind": "postgraduate_taught",
                        "course_source_url": COURSE_URL,
                        "annual_tuition": str(fee),
                        "currency": "GBP",
                        "study_year": 1,
                        "fee_method": "reviewed_named_course_fee",
                        "placement_fee": str(placement_fee),
                        "placement_fee_currency": "GBP",
                        "placement_fee_included_in_tuition": False,
                        "fee_note": annotation,
                        "scholarship_status": "verified_rule_eligibility_required",
                        "deposit_status": "offer_letter_amount_required",
                        "fee_categories": [{
                            "source_fee_category": "International",
                            "academic_year": YEAR,
                            "currency": "GBP",
                            "annual_tuition": str(fee),
                            "raw_annual_fee": "£23,820",
                            "annotation": annotation,
                            "footnote_marker": "",
                            "amount_requires_review": False,
                        }],
                    },
                },
            }

            name = f"Global Citizens Scholarship — Sciences PG ({YEAR})"
            awards = list(
                Scholarship.objects.select_for_update().filter(
                    university=university,
                    name=name,
                    level="PG",
                )
            )

            if len(awards) != 1:
                raise CommandError(
                    "The existing reviewed PG science scholarship is required."
                )

            rule = (
                ScholarshipRule.objects.select_for_update()
                .filter(scholarship=awards[0])
                .first()
            )

            if (
                rule is None
                or rule.verified is not True
                or rule.source_url != SCHOLARSHIP_URL
                or rule.rules.get("academic_year") != YEAR
                or rule.rules.get("kind") != "fixed"
                or rule.rules.get("fee_basis_confirmed") is not True
                or rule.rules.get("study_years") != [1]
                or Decimal(str(rule.rules.get("value"))) != Decimal("3800")
            ):
                raise CommandError("Existing PG scholarship rule needs review.")

            rule_before = copy.deepcopy(rule.rules)
            rule_after = copy.deepcopy(rule.rules)
            ids = rule_after.get("conditions", {}).get("course_id")

            if not isinstance(ids, list) or not all(
                isinstance(value, str) for value in ids
            ):
                raise CommandError("Invalid scholarship course scope.")

            rule_after["conditions"]["course_id"] = sorted(
                set(ids) | {COURSE_ID}
            )

            fee_changed = before != after
            rule_changed = rule_before != rule_after

            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "mode": "apply" if options["apply"] else "preview",
                        "university_id": "UK002",
                        "course_id": COURSE_ID,
                        "evidence_report": str(evidence_path),
                        "fee_changed": fee_changed,
                        "scholarship_scope_changed": rule_changed,
                        "before": before,
                        "after": after,
                        "scholarship_rule_before": rule_before,
                        "scholarship_rule_after": rule_after,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            if options["apply"]:
                if fee_changed:
                    course.start_month = "September"
                    course.start_year = 2027
                    course.course_url = COURSE_URL
                    course.work_placement = True
                    course.save(update_fields=[
                        "start_month",
                        "start_year",
                        "course_url",
                        "work_placement",
                        "updated_at",
                    ])

                    tuition.tuition_fee = fee
                    tuition.save(update_fields=["tuition_fee"])

                    TuitionSource.objects.update_or_create(
                        tuition=tuition,
                        defaults=after["source"],
                    )

                if rule_changed:
                    rule.rules = rule_after
                    rule.reviewed_at = now
                    rule.save(update_fields=["rules", "reviewed_at"])

                if fee_changed or rule_changed:
                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=COURSE_ID,
                        residency="INTL",
                        before={
                            **before,
                            "scholarship_rules": rule_before,
                        },
                        after={
                            **after,
                            "scholarship_rules": rule_after,
                        },
                    )

        self.stdout.write(
            f"Teaching-year fee: GBP {fee}; "
            f"{'update' if fee_changed else 'unchanged'}"
        )
        self.stdout.write(
            f"Separate placement charge: GBP {placement_fee}"
        )
        self.stdout.write(
            "First-year scholarship scope: "
            f"{'update' if rule_changed else 'unchanged'}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Applied reviewed fee, placement details and scholarship scope."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Stored scholarship and deposit amounts unchanged. "
            "The scholarship remains conditional on eligibility."
        )