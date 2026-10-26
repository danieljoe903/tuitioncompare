import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from app.models import Course
import copy
from decimal import Decimal

from django.db import transaction

from app.models import (
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
)

from .preview_aru_specialist_routes import Command as RouteValidator


TARGETS = {
    "C_UK003_02": {
        "slug": "undergraduate/animal-management-pet-behaviour-and-welfare",
        "expected_title": "BSc (Hons) Animal Behaviour",
        "replacement": True,
        "changes": {
            "title": (
                "BSc (Hons) Animal Management "
                "(Pet Behaviour and Welfare)"
            ),
            "level": "UG",
            "duration": "3 Years",
            "location": "Writtle Campus",
            "start_month": "September",
            "start_year": 2027,
            "ucas_cao_code": "D304",
        },
        "fee": "18825.00",
        "academic_year": "2027/28",
        "route_markers": [
            "Animal Management (Pet Behaviour and Welfare)",
            "Writtle",
            "D304",
            "3 years",
            "September 2027",
        ],
        "schema_name": "Animal Management (Pet Behaviour and Welfare)",
        "schema_award": "BSc (Hons)",
        "schema_workload": "P3Y",
        "schema_campus": "Writtle",
        "scholarship_note": (
            "Review for the lower first-year undergraduate award "
            "at Writtle. Do not automatically extend the higher award."
        ),
    },
    "C_UK003_54": {
        "slug": "postgraduate/crop-production",
        "expected_title": "MSc Sustainable Agriculture",
        "replacement": True,
        "changes": {
            "title": "MSc Crop Production",
            "level": "PG",
            "duration": "1 Year",
            "location": "Writtle Campus",
            "start_month": "September",
            "start_year": 2027,
        },
        "fee": "18825.00",
        "academic_year": "2027/28",
        "route_markers": [
            "Crop Production",
            "Writtle",
            "September 2027",
            "1 year full-time",
            "16 months",
        ],
        "schema_name": "Crop Production",
        "schema_award": "MSc",
        "schema_workload": "P1Y",
        "schema_campus": "Writtle",
        "scholarship_note": (
            "Review the saved International Postgraduate Scholarship "
            "for ARU graduates. An MSc place alone does not establish "
            "eligibility. This preview covers September entry only."
        ),
    },
    "C_UK003_71": {
        "slug": (
            "postgraduate/"
            "construction-and-civil-engineering-management"
        ),
        "expected_title": "MSc Civil Engineering",
        "replacement": True,
        "changes": {
            "title": "MSc Construction and Civil Engineering Management",
            "level": "PG",
            "duration": "1 Year",
            "location": "Chelmsford Campus",
            "start_month": "September",
            "start_year": 2027,
        },
        "fee": "20925.00",
        "academic_year": "2027/28",
        "route_markers": [
            "Construction and Civil Engineering Management",
            "Chelmsford",
            "September 2027",
            "1 year full-time",
        ],
        "schema_name": "Construction and Civil Engineering Management",
        "schema_award": "MSc",
        "schema_workload": "P1Y",
        "schema_campus": "Chelmsford",
        "scholarship_note": (
            "Review the saved International Postgraduate Scholarship "
            "for ARU graduates. This preview excludes part-time "
            "and distance-learning routes."
        ),
    },
}


class Command(RouteValidator):
    help = (
        "Preview three explicit ARU replacements using saved evidence. "
        "No database updates."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--evidence",
            help=(
                "Optional evidence path. Otherwise find the newest "
                "campus-intake report containing these three courses."
            ),
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Save the reviewed replacements and tuition evidence.",
        )
        parser.add_argument(
            "--accept-replacements",
            action="store_true",
            help="Explicitly accept all three course replacements.",
        )

    def read_report(self, path):
        return json.loads(
            path.read_text(encoding="utf-8-sig")
        )

    def find_evidence(self, supplied):
        root = Path(settings.BASE_DIR)

        if supplied:
            path = Path(supplied)
            return path if path.is_absolute() else root / path

        candidates = []

        for path in (root / "logs" / "aru").glob(
            "campus-intakes-*/campus-intake-evidence.json"
        ):
            try:
                report = self.read_report(path)
                ids = {
                    entry["database_course"]["course_id"]
                    for entry in report.get("courses", [])
                }
            except (OSError, ValueError, KeyError, TypeError):
                continue

            if (
                report.get("university_id") == "UK003"
                and set(TARGETS).issubset(ids)
            ):
                candidates.append(path)

        if not candidates:
            raise CommandError(
                "No evidence report containing the three courses "
                "was found. Supply --evidence explicitly."
            )

        return max(candidates, key=lambda item: item.parent.name)

    def validate_schema(self, page, target):
        schemas = []

        for script in page.get("scripts", []):
            value = script.get("parsed_json")

            if isinstance(value, dict):
                schemas.append(value)
            elif isinstance(value, list):
                schemas.extend(
                    item for item in value
                    if isinstance(item, dict)
                )

        course_schemas = [
            item for item in schemas
            if item.get("@type") == "Course"
            and item.get("name") == target["schema_name"]
            and item.get("award") == target["schema_award"]
        ]

        if len(course_schemas) != 1:
            raise ValueError(
                "Expected one matching course name and award "
                "in the saved structured data."
            )

        instances = course_schemas[0].get("hasCourseInstance", [])

        if isinstance(instances, dict):
            instances = [instances]

        matching = []

        for instance in instances:
            if not isinstance(instance, dict):
                continue

            locations = instance.get("location", [])

            if isinstance(locations, dict):
                locations = [locations]

            campus_matches = any(
                isinstance(location, dict)
                and location.get("name") == target["schema_campus"]
                for location in locations
            )

            start = (
                instance.get("startdate")
                or instance.get("startDate")
                or ""
            )

            if (
                instance.get("name") == "Full-time"
                and instance.get("courseMode") == "Onsite"
                and str(start).startswith("2027-09")
                and instance.get("courseWorkload")
                == target["schema_workload"]
                and campus_matches
            ):
                matching.append(instance)

        if len(matching) != 1:
            raise ValueError(
                "Cannot isolate the September 2027 standard "
                "full-time route, duration and campus."
            )

    def apply_proposals(self, proposals, report, evidence_path):
        def money(value):
            amount = Decimal(str(value))

            if not amount.is_finite() or amount < 0:
                raise ValueError("Invalid tuition amount.")

            return str(amount.quantize(Decimal("0.01")))

        def source_state(source):
            if source is None:
                return None

            return {
                "academic_year": source.academic_year,
                "source_url": source.source_url,
                "source_course_code": source.source_course_code,
                "source_course_name": source.source_course_name,
                "fee_basis": source.fee_basis,
                "raw_fees": copy.deepcopy(source.raw_fees),
            }

        applied = []

        with transaction.atomic():
            # Lock ARU courses before checking for duplicate route URLs.
            list(
                Course.objects.select_for_update()
                .filter(university_id="UK003")
                .order_by("course_id")
            )

            list(
                Tuition.objects.select_for_update()
                .filter(
                    course__university_id="UK003",
                    course_id__in=TARGETS,
                    residency="INTL",
                )
                .order_by("course_id")
            )

            list(
                TuitionSource.objects.select_for_update()
                .filter(
                    tuition__course__university_id="UK003",
                    tuition__course_id__in=TARGETS,
                    tuition__residency="INTL",
                )
                .order_by("tuition_id")
            )

            # Revalidate every proposal before making any changes.
            for proposal in proposals:
                course_id = proposal["course_id"]
                target = TARGETS[course_id]

                page, _, source_url = self.load_page(
                    report,
                    evidence_path,
                    course_id,
                    target,
                )
                self.validate_schema(page, target)

                duplicates = list(
                    Course.objects.filter(
                        university_id="UK003",
                        course_url__in=[
                            source_url.rstrip("/"),
                            source_url.rstrip("/") + "/",
                        ],
                    )
                    .exclude(course_id=course_id)
                    .values_list("course_id", flat=True)
                )

                if duplicates:
                    raise CommandError(
                        f"{course_id}: another record uses this route: "
                        + ", ".join(duplicates)
                        + ". Nothing was applied."
                    )

                fresh = self.make_proposal(
                    report,
                    evidence_path,
                    course_id,
                    target,
                )

                original = {
                    key: value
                    for key, value in proposal.items()
                    if key != "reviewed_route"
                }

                if fresh != original:
                    raise CommandError(
                        f"{course_id}: database inputs changed during "
                        "the run. Nothing was applied."
                    )

            for proposal in proposals:
                course_id = proposal["course_id"]
                target = TARGETS[course_id]

                course = Course.objects.get(
                    university_id="UK003",
                    course_id=course_id,
                )
                tuition = Tuition.objects.get(
                    course=course,
                    residency="INTL",
                    currency="GBP",
                )
                source = TuitionSource.objects.filter(
                    tuition=tuition
                ).first()

                course_fields = sorted(
                    set(target["changes"]) | {"course_url"}
                )

                before_course = {
                    field: getattr(course, field)
                    for field in course_fields
                }

                before_source = source_state(source)

                before = {
                    "course": before_course,
                    "tuition": {
                        "tuition_fee": money(tuition.tuition_fee),
                        "currency": tuition.currency,
                    },
                    "source": before_source,
                }

                for field, change in proposal["course_changes"].items():
                    setattr(course, field, change["after"])

                published_amount = money(
                    proposal["tuition"]["proposed_amount"]
                )
                evidence = proposal["evidence"]

                raw = (
                    copy.deepcopy(source.raw_fees)
                    if source is not None
                    and isinstance(source.raw_fees, dict)
                    else {}
                )

                history = raw.get("route_review_history", [])

                if not isinstance(history, list):
                    raise CommandError(
                        f"{course_id}: existing replacement history "
                        "requires review. Nothing was applied."
                    )

                history = copy.deepcopy(history)

                if proposal["course_changes"]:
                    history.append({
                        "mapping": "explicit_replacement",
                        "replacement_accepted": True,
                        "before": before_course,
                        "after": {
                            field: getattr(course, field)
                            for field in course_fields
                        },
                        "source_url": evidence["source_url"],
                        "source_sha256": evidence["source_sha256"],
                        "evidence_generated_at": (
                            evidence["evidence_generated_at"]
                        ),
                    })

                route_changed = (
                    bool(proposal["course_changes"])
                    or source is None
                    or source.academic_year != "2027/28"
                    or source.source_url.rstrip("/")
                    != evidence["source_url"].rstrip("/")
                )

                note = (
                    "Published annual international tuition for "
                    "September 2027 entry on the reviewed standard "
                    "full-time route. Foundation, part-time and "
                    "distance-learning options are excluded."
                )

                if course_id == "C_UK003_54":
                    note += (
                        " This comparison uses the one-year September "
                        "route, not the longer January route."
                    )

                raw.update({
                    "verified": True,
                    "fee_verification_status": "verified",
                    "verification_reason": "",
                    "academic_year": "2027/28",
                    "source_fee_category": "International",
                    "currency": "GBP",
                    "annual_tuition": published_amount,
                    "fee_basis": "annual_tuition",
                    "fee_method": "reviewed_explicit_replacement",
                    "course_kind": (
                        "undergraduate_taught"
                        if course.level == "UG"
                        else "postgraduate_taught"
                    ),
                    "study_mode": "full_time",
                    "study_year": 1,
                    "campus": target["schema_campus"],
                    "intake": "September 2027",
                    "course_source_url": evidence["source_url"],
                    "source_sha256": evidence["source_sha256"],
                    "fee_note": note,
                    "reviewed_route": proposal["reviewed_route"],
                    "route_review_history": history,
                    "fee_categories": [{
                        "source_fee_category": "International",
                        "academic_year": "2027/28",
                        "currency": "GBP",
                        "annual_tuition": published_amount,
                        "raw_annual_fee": (
                            f"£{Decimal(published_amount):,.2f}"
                        ),
                        "annotation": note,
                        "amount_requires_review": False,
                    }],
                })

                # Remove contradictory fee-review messages while retaining
                # unrelated scholarship/deposit notes and prior evidence.
                fee_review = raw.get("fee_review")

                if isinstance(fee_review, dict):
                    raw["fee_review"] = {
                        **fee_review,
                        "status": "resolved_by_explicit_replacement",
                        "reason": note,
                    }

                display_notes = raw.get("display_notes")

                if isinstance(display_notes, dict):
                    raw["display_notes"] = {
                        **display_notes,
                        "fee": note,
                    }

                # Reset applicability only when the course identity/period
                # changes. A repeat run preserves later reconciliation.
                if route_changed:
                    previous_review = raw.get("scholarship_review")
                    if not isinstance(previous_review, dict):
                        previous_review = {}

                    previous_deposit = raw.get("deposit_review")
                    if not isinstance(previous_deposit, dict):
                        previous_deposit = {}

                    raw["scholarship_status"] = (
                        "applicability_unconfirmed"
                    )
                    raw["scholarship_review"] = {
                        **previous_review,
                        "status": "applicability_unconfirmed",
                        "note": target["scholarship_note"],
                    }

                    raw["deposit_status"] = "applicability_pending"
                    raw["deposit_review"] = {
                        **previous_deposit,
                        "status": "applicability_pending",
                        "note": (
                            "The course page publishes a £4,000 "
                            "deposit or sponsorship-letter requirement. "
                            "Saved policy scope requires separate "
                            "reconciliation for this replacement route."
                        ),
                    }

                after_source = {
                    "academic_year": "2027/28",
                    "source_url": evidence["source_url"],
                    "source_course_code": (
                        course.ucas_cao_code or ""
                    ),
                    "source_course_name": course.title,
                    "fee_basis": "annual_tuition",
                    "raw_fees": raw,
                }

                after = {
                    "course": {
                        field: getattr(course, field)
                        for field in course_fields
                    },
                    "tuition": {
                        "tuition_fee": published_amount,
                        "currency": tuition.currency,
                    },
                    "source": after_source,
                }

                if before == after:
                    continue

                if proposal["course_changes"]:
                    course.save(
                        update_fields=[
                            *proposal["course_changes"],
                            "updated_at",
                        ]
                    )

                if money(tuition.tuition_fee) != published_amount:
                    tuition.tuition_fee = Decimal(published_amount)
                    tuition.save(update_fields=["tuition_fee"])

                if before_source != after_source:
                    TuitionSource.objects.update_or_create(
                        tuition=tuition,
                        defaults=after_source,
                    )

                TuitionUpdateAudit.objects.create(
                    tuition=tuition,
                    course_identifier=course_id,
                    residency="INTL",
                    before=before,
                    after=after,
                )

                applied.append({
                    "course_id": course_id,
                    "before": before,
                    "after": after,
                })

        return applied

    def handle(self, *args, **options):
        evidence_path = self.find_evidence(options["evidence"])

        try:
            report = self.read_report(evidence_path)
        except (OSError, ValueError) as exc:
            raise CommandError(
                f"Cannot read evidence: {exc}"
            ) from exc

        if report.get("university_id") != "UK003":
            raise CommandError("Evidence is not for ARU UK003.")

        proposals = []
        blocked = []

        for course_id, target in TARGETS.items():
            try:
                page, _, source_url = self.load_page(
                    report,
                    evidence_path,
                    course_id,
                    target,
                )

                self.validate_schema(page, target)

                duplicates = list(
                    Course.objects.filter(
                        university_id="UK003",
                        course_url__in=[
                            source_url.rstrip("/"),
                            source_url.rstrip("/") + "/",
                        ],
                    )
                    .exclude(course_id=course_id)
                    .values_list("course_id", flat=True)
                )

                if duplicates:
                    raise ValueError(
                        "Another stored course already uses this URL: "
                        + ", ".join(duplicates)
                    )

                proposal = self.make_proposal(
                    report,
                    evidence_path,
                    course_id,
                    target,
                )

                proposal["reviewed_route"] = {
                    "entry": "September 2027",
                    "academic_year": "2027/28",
                    "mode": "full_time",
                    "campus": target["schema_campus"],
                    "excluded_routes": [
                        "foundation",
                        "part_time",
                        "distance_learning",
                    ],
                }

                proposals.append(proposal)

                fee = proposal["tuition"]

                self.stdout.write(
                    f"{course_id}: GBP {fee['stored_amount']} -> "
                    f"{fee['proposed_amount']}; September 2027 / "
                    "2027/28 — explicit replacement proposal"
                )

                for field, change in proposal["course_changes"].items():
                    self.stdout.write(
                        f"  {field}: {change['before']} -> "
                        f"{change['after']}"
                    )

            except (
                ValueError,
                OSError,
                Course.DoesNotExist,
                Course.MultipleObjectsReturned,
            ) as exc:
                blocked.append({
                    "course_id": course_id,
                    "reason": str(exc),
                })
                self.stderr.write(
                    f"{course_id}: BLOCKED — {exc}"
                )

        folder = Path(settings.BASE_DIR) / "logs" / "aru"
        folder.mkdir(parents=True, exist_ok=True)

        output = folder / (
            "land-engineering-preview-"
            + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )

        output.write_text(
            json.dumps(
                {
                    "generated_at": timezone.now().isoformat(),
                    "university_id": "UK003",
                    "mode": "preview",
                    "database_changes": False,
                    "evidence_report": str(evidence_path),
                    "proposals": proposals,
                    "blocked": blocked,
                    "scholarship_scopes_changed": False,
                    "deposit_policies_changed": False,
                },
                cls=DjangoJSONEncoder,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            f"\nVerified fee proposals: {len(proposals)}"
        )
        self.stdout.write(f"Blocked: {len(blocked)}")
        self.stdout.write(f"Report: {output}")
        if blocked:
            self.stdout.write(
                "No database records changed."
            )
            raise CommandError(
                "Some proposals require review. See the saved report."
            )

        if not options["apply"]:
            self.stdout.write(
                "Preview only. No database records changed."
            )
            self.stdout.write(
                "Replacements require explicit acceptance. Scholarship "
                "and deposit scopes have not been extended."
            )
            return

        if not options["accept_replacements"]:
            raise CommandError(
                "Use --apply --accept-replacements to accept "
                "the three reviewed course replacements. "
                "No database records changed."
            )

        applied = self.apply_proposals(
            proposals,
            report,
            evidence_path,
        )

        saved_report = json.loads(
            output.read_text(encoding="utf-8")
        )
        saved_report.update({
            "mode": "apply",
            "database_changes": bool(applied),
            "replacements_accepted": True,
            "changed_records": len(applied),
            "applied_changes": applied,
        })

        output.write_text(
            json.dumps(
                saved_report,
                cls=DjangoJSONEncoder,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            f"Applied changed records: {len(applied)}"
        )
        self.stdout.write(
            "Accepted replacements, international tuition, source "
            "evidence and audit records saved."
        )
        self.stdout.write(
            "Scholarship rules, stored scholarship amounts and "
            "deposit policies unchanged. Replacement-route "
            "scholarship/deposit scopes require reconciliation."
        )