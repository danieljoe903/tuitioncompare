import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from app.models import (
    Course,
    DepositPolicy,
    ScholarshipRule,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
)


REVIEWS = {
    "C_UK003_18": {
        "reason": "degree_route_unconfirmed",
        "note": (
            "A current BSc Veterinary Nursing route at Writtle has "
            "not been verified. The reviewed Writtle listing describes "
            "college qualifications, including a Level 3 apprenticeship. "
            "Those qualifications must not replace the stored BSc."
        ),
    },
    "C_UK003_22": {
        "reason": "possible_duplicate_identity_unconfirmed",
        "note": (
            "The stored MSc Data Science has no verified course-specific "
            "source. Applied Data Science (Conversion) is already "
            "represented by C_UK003_76. The records have not been "
            "proven identical. Do not copy its fee or merge automatically."
        ),
    },
    "C_UK003_26": {
        "reason": "international_fee_to_be_confirmed",
        "note": (
            "The ARU London MPH source states that international tuition "
            "for 2027/28 is to be confirmed. Its £19,500 international "
            "fee belongs to 2026/27 and must not be assigned to the "
            "stored September 2027 intake."
        ),
    },
    "C_UK003_41": {
        "reason": "current_route_and_intake_unconfirmed",
        "note": (
            "Historical ARU evidence names Marine Biology with "
            "Biodiversity and Conservation. A current course-specific "
            "intake and applicable international fee have not been "
            "verified for this database record."
        ),
    },
    "C_UK003_43": {
        "reason": "former_course_url_redirect",
        "note": (
            "The former Ecology and Conservation course URL redirects "
            "to an Animal and Veterinary subject listing. This does "
            "not verify a current course-specific intake or fee, "
            "and does not alone prove discontinuation."
        ),
    },
    "C_UK003_46": {
        "reason": "international_fee_applicability_unconfirmed",
        "note": (
            "The reviewed Nursing (Mental Health) page identifies the "
            "route but does not verify an applicable international "
            "tuition amount. UK fees and training grants must not "
            "be substituted. This is not a blanket nationality exclusion."
        ),
    },
    "C_UK003_52": {
        "reason": "distance_fee_international_applicability_unconfirmed",
        "note": (
            "The captured page publishes a £9,450 total distance-learning "
            "course fee for 2027/28 under its UK-student fee presentation. "
            "International applicability remains unconfirmed. This is "
            "not a verified annual international fee."
        ),
    },
    "C_UK003_56": {
        "reason": "current_international_route_unconfirmed",
        "note": (
            "The Horticulture capture identifies a three-year part-time "
            "Writtle route without a confirmed intake year. Embedded "
            "fee data contains a 2025/26 UK part-time price. A current "
            "applicable international route and fee remain unverified."
        ),
    },
    "C_UK003_66": {
        "reason": "course_url_redirect",
        "note": (
            "The Music Performance course URL redirects to an Arts, "
            "Creative and Media subject listing. A current route, "
            "intake and applicable international fee are unverified. "
            "The redirect alone does not prove discontinuation."
        ),
    },
    "C_UK003_82": {
        "reason": "possible_duplicate_identity_unconfirmed",
        "note": (
            "The stored BA International Relations has no verified "
            "course-specific source. The combined Politics and "
            "International Relations route is represented by "
            "C_UK003_81. Do not duplicate that mapping or its fee "
            "without establishing the relationship."
        ),
    },
    "C_UK003_87": {
        "reason": "blended_route_international_fee_unconfirmed",
        "note": (
            "The reviewed Chelmsford route is a two-year accelerated "
            "blended Early Childhood Studies degree. Its applicable "
            "international tuition and application eligibility remain "
            "unconfirmed. UK and separate distance-learning prices "
            "must not be substituted."
        ),
    },
    "C_UK003_88": {
        "reason": "international_route_and_fee_unconfirmed",
        "note": (
            "The reviewed blended Social Work route publishes UK fees "
            "and application guidance for UK applicants and specified "
            "status groups. International application eligibility and "
            "the applicable fee remain unconfirmed. This is not a "
            "blanket nationality exclusion."
        ),
    },
    "C_UK003_89": {
        "reason": "international_fee_applicability_unconfirmed",
        "note": (
            "The reviewed Nursing (Children) page identifies the route "
            "but does not verify an applicable international tuition "
            "amount. UK fees and training grants must not be substituted. "
            "This is not a blanket nationality exclusion."
        ),
    },
    "C_UK003_94": {
        "reason": "international_fee_applicability_unconfirmed",
        "note": (
            "The September 2027 BA 3D Animation route is identified, "
            "but an applicable international tuition amount has not "
            "been verified. UK tuition must not be substituted."
        ),
    },
    "C_UK003_104": {
        "reason": "intake_fee_year_mismatch",
        "note": (
            "The Global Public Health route advertises January 2027 "
            "entry, while the captured international fee of £18,825 "
            "is labelled 2027/28. January 2027 requires applicable "
            "2026/27 evidence. Do not assign the later fee year "
            "or invent a September intake."
        ),
    },
}


class Command(BaseCommand):
    help = (
        "Preview or save unavailable-fee review metadata for "
        "15 unresolved ARU records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Save review metadata without changing prices.",
        )

    @staticmethod
    def check_saved_scopes():
        target_ids = set(REVIEWS)

        for rule in ScholarshipRule.objects.filter(
            scholarship__university_id="UK003",
            verified=True,
        ):
            data = rule.rules

            if not isinstance(data, dict):
                continue

            conditions = data.get("conditions", {})

            if not isinstance(conditions, dict):
                continue

            scope = conditions.get("course_id", [])

            if isinstance(scope, list):
                overlap = target_ids.intersection(scope)

                if overlap:
                    raise CommandError(
                        "Pending courses appear in verified scholarship "
                        f"rule {rule.pk}: {sorted(overlap)}. "
                        "Review that scope before applying this batch."
                    )

        for policy in DepositPolicy.objects.filter(
            university_id="UK003",
            verified=True,
        ):
            data = policy.rules

            if not isinstance(data, dict):
                continue

            scope = data.get("course_ids", [])

            if isinstance(scope, list):
                overlap = target_ids.intersection(scope)

                if overlap:
                    raise CommandError(
                        "Pending courses appear in verified deposit "
                        f"policy {policy.pk}: {sorted(overlap)}. "
                        "Review that scope before applying this batch."
                    )

    @staticmethod
    def source_snapshot(source):
        if source is None:
            return None

        return {
            "academic_year": source.academic_year,
            "source_url": source.source_url,
            "source_course_name": source.source_course_name,
            "source_course_code": source.source_course_code,
            "fee_basis": source.fee_basis,
            "raw_fees": deepcopy(source.raw_fees),
        }

    @staticmethod
    def write_report(path, report):
        path.parent.mkdir(parents=True, exist_ok=True)

        path.write_text(
            json.dumps(
                report,
                cls=DjangoJSONEncoder,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def handle(self, *args, **options):
        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")

        output = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aru"
            / f"remaining-15-review-{stamp}.json"
        )

        plans = []
        skipped = []

        with transaction.atomic():
            self.check_saved_scopes()

            for course_id, review in sorted(REVIEWS.items()):
                course = Course.objects.select_for_update().get(
                    university_id="UK003",
                    course_id=course_id,
                )

                tuitions = list(
                    Tuition.objects.select_for_update().filter(
                        course=course,
                        residency="INTL",
                    )
                )

                if (
                    len(tuitions) != 1
                    or tuitions[0].currency != "GBP"
                ):
                    raise CommandError(
                        f"{course_id}: expected one GBP "
                        "international tuition. No changes committed."
                    )

                tuition = tuitions[0]

                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                if source is not None and not isinstance(
                    source.raw_fees, dict
                ):
                    raise CommandError(
                        f"{course_id}: invalid source metadata. "
                        "No changes committed."
                    )

                # Never downgrade a subsequently verified course.
                if (
                    source is not None
                    and source.raw_fees.get("verified") is True
                ):
                    skipped.append(course_id)
                    continue

                before = self.source_snapshot(source)
                raw = deepcopy(source.raw_fees) if source else {}

                raw.update({
                    "verified": False,
                    "annual_tuition": None,
                    "currency": "GBP",
                    "source_fee_category": "International",
                    "fee_basis": "unavailable",
                    "fee_categories": [],
                    "fee_note": review["note"],
                    "verification_reason": review["note"],
                    "review_status": "pending_confirmation",
                    "review_reason_code": review["reason"],
                })

                # Retain existing eligibility questions and notes.
                # Saving a review does not establish an award.
                raw["scholarship_status"] = (
                    "applicability_unconfirmed"
                )

                if course_id == "C_UK003_52":
                    raw["deposit_status"] = (
                        "campus_pre_cas_policy_not_applicable"
                    )
                else:
                    raw["deposit_status"] = (
                        "applicability_unconfirmed"
                    )

                after = {
                    "academic_year": (
                        source.academic_year if source else ""
                    ),
                    "source_url": (
                        source.source_url if source else ""
                    ),
                    "source_course_name": (
                        source.source_course_name
                        if source else course.title
                    ),
                    "source_course_code": (
                        source.source_course_code if source else ""
                    ),
                    "fee_basis": "unavailable",
                    "raw_fees": raw,
                }

                plan = {
                    "course_id": course_id,
                    "tuition_id": tuition.pk,
                    "title": course.title,
                    "reason": review["reason"],
                    "note": review["note"],
                    "stored_tuition_preserved": str(
                        tuition.tuition_fee
                    ),
                    "before": before,
                    "after": after,
                    "changed": before != after,
                }

                plans.append(plan)

            changed_count = sum(
                plan["changed"] for plan in plans
            )

            report = {
                "generated_at": timezone.now().isoformat(),
                "university_id": "UK003",
                "mode": "preview",
                "database_changes": False,
                "reviewed_records": len(plans),
                "proposed_changes": changed_count,
                "already_verified_and_skipped": skipped,
                "reviews": plans,
                "fees_newly_verified": 0,
                "scholarship_rules_changed": False,
                "deposit_policies_changed": False,
            }

            try:
                self.write_report(output, report)
            except OSError as exc:
                raise CommandError(
                    f"Cannot save preview report: {exc}. "
                    "No database changes committed."
                ) from exc

            if options["apply"]:
                for plan in plans:
                    if not plan["changed"]:
                        continue

                    TuitionSource.objects.update_or_create(
                        tuition_id=plan["tuition_id"],
                        defaults=plan["after"],
                    )

                    TuitionUpdateAudit.objects.create(
                        tuition_id=plan["tuition_id"],
                        course_identifier=plan["course_id"],
                        residency="INTL",
                        before={"source": plan["before"]},
                        after={"source": plan["after"]},
                    )

        if options["apply"]:
            report["mode"] = "apply"
            report["database_changes"] = changed_count > 0

            try:
                self.write_report(output, report)
            except OSError as exc:
                raise CommandError(
                    "Database changes were committed, but the "
                    f"final report could not be updated: {exc}"
                ) from exc

        for plan in plans:
            state = "save review" if plan["changed"] else "unchanged"

            self.stdout.write(
                f"{plan['course_id']}: {state} "
                f"— {plan['reason']}"
            )

        self.stdout.write(
            f"\nRecords reviewed: {len(plans)}"
        )
        self.stdout.write(
            f"Already verified and skipped: {len(skipped)}"
        )
        self.stdout.write(
            f"Proposed/updated records: {changed_count}"
        )
        self.stdout.write("Newly verified fees: 0")
        self.stdout.write(f"Report: {output}")

        if options["apply"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "Unavailable-fee review metadata and audits saved."
                )
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )

        self.stdout.write(
            "Course identities, active status, numeric tuition, "
            "stored discounts and deposits preserved. "
            "These records remain pending confirmation."
        )