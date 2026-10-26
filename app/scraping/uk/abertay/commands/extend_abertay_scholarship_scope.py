import json
from copy import deepcopy
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import ScholarshipRule, TuitionSource


YEAR = "2026/27"

SCHOLARSHIP_URL = (
    "https://www.abertay.ac.uk/study-apply/money-fees-and-funding/"
    "scholarships/abertay-international-scholarship/"
)


class Command(BaseCommand):
    help = (
        "Extend Abertay scholarship rules to verified course fees. "
        "Does not change tuition prices or stored discount amounts."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        report = {
            "academic_year": YEAR,
            "status": "prepared",
            "rules": [],
        }

        with transaction.atomic():
            sources = list(
                TuitionSource.objects.select_for_update()
                .filter(
                    tuition__course__university__name__iexact=(
                        "Abertay University"
                    ),
                    tuition__course__university__country="UK",
                    tuition__course__start_year=2026,
                    tuition__residency="INTL",
                    tuition__currency="GBP",
                    academic_year=YEAR,
                    fee_basis="annual_tuition",
                )
                .select_related("tuition__course")
            )

            scopes = {"UG": [], "PG": []}

            for source in sources:
                tuition = source.tuition
                course = tuition.course
                raw = source.raw_fees

                if course.level == "UG":
                    scope = "UG"

                elif (
                    course.level in {"PG", "PGT"}
                    and raw.get("course_kind")
                    == "postgraduate_taught"
                ):
                    scope = "PG"

                else:
                    # Research courses are not included in the taught award.
                    continue

                categories = [
                    category
                    for category in raw.get("fee_categories", [])
                    if category.get("source_fee_category")
                    == "International"
                ]

                if len(categories) != 1:
                    raise CommandError(
                        f"{course.course_id}: international category "
                        "is missing or ambiguous."
                    )

                category = categories[0]

                try:
                    published_fee = Decimal(
                        str(category["annual_tuition"])
                    )
                except (KeyError, ValueError, ArithmeticError) as exc:
                    raise CommandError(
                        f"{course.course_id}: invalid source fee."
                    ) from exc

                if (
                    category.get("academic_year") != YEAR
                    or category.get("currency") != "GBP"
                    or category.get("amount_requires_review") is not False
                    or category.get("footnote_marker")
                    or not published_fee.is_finite()
                    or published_fee <= 0
                    or published_fee != tuition.tuition_fee
                ):
                    raise CommandError(
                        f"{course.course_id}: source fee needs review."
                    )

                scopes[scope].append(course.course_id)

            for level in ("UG", "PG"):
                course_ids = sorted(scopes[level])

                if not course_ids:
                    raise CommandError(
                        f"No verified {level} course fees found."
                    )

                if len(course_ids) != len(set(course_ids)):
                    raise CommandError(
                        f"Duplicate {level} tuition records found."
                    )

                found = list(
                    ScholarshipRule.objects.select_for_update()
                    .filter(
                        scholarship__university__name__iexact=(
                            "Abertay University"
                        ),
                        scholarship__name__iexact=(
                            "Abertay International Scholarship"
                        ),
                        scholarship__level=level,
                    )[:2]
                )

                if len(found) != 1:
                    raise CommandError(
                        f"Expected exactly one {level} base award rule."
                    )

                rule = found[0]
                before = deepcopy(rule.rules)

                if (
                    rule.verified is not True
                    or rule.source_url != SCHOLARSHIP_URL
                    or before.get("academic_year") != YEAR
                    or before.get("benefit") != "tuition"
                    or before.get("kind") != "fixed_maximum"
                    or before.get("currency") != "GBP"
                    or before.get("award_status") != "conditional"
                    or not isinstance(before.get("conditions"), dict)
                ):
                    raise CommandError(
                        f"{level}: existing base award needs review."
                    )

                previous_ids = set(
                    before["conditions"].get("course_id", [])
                )

                if not previous_ids.issubset(set(course_ids)):
                    raise CommandError(
                        f"{level}: previously included courses no longer "
                        "have matching verified fees."
                    )

                after = deepcopy(before)

                # Preserve the award's own amount and calculation type.
                # Only extend the verified course scope.
                after["fee_basis_confirmed"] = True
                after["conditions"]["course_id"] = course_ids
                after["conditions"]["level"] = (
                    ["UG"] if level == "UG" else ["PG", "PGT"]
                )

                if level == "PG":
                    after["conditions"]["postgraduate_taught"] = [True]
                    after["conditions"]["pg_2_1_equivalent_met"] = [True]

                after["review_notes"] = (
                    "First compared study year only. Apply this base "
                    "maximum only to the listed verified course fees "
                    "when all eligibility conditions are confirmed. "
                    "Other course-specific awards retain their own "
                    "amounts, eligibility and combination rules."
                )

                report["rules"].append({
                    "scholarship_id": rule.scholarship_id,
                    "level": level,
                    "course_count": len(course_ids),
                    "before": before,
                    "after": after,
                    "changed": before != after,
                })

            log_dir = (
                settings.BASE_DIR
                / "logs"
                / "scholarship-reconciliation"
            )
            log_dir.mkdir(parents=True, exist_ok=True)

            output = log_dir / (
                "scope-"
                + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
                + ".json"
            )

            output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            if options["apply"]:
                for change in report["rules"]:
                    if not change["changed"]:
                        continue

                    rule = ScholarshipRule.objects.get(
                        scholarship_id=change["scholarship_id"]
                    )
                    rule.rules = change["after"]
                    rule.reviewed_at = timezone.now()
                    rule.save(
                        update_fields=["rules", "reviewed_at"]
                    )

        report["status"] = (
            "committed" if options["apply"] else "preview_only"
        )
        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        for change in report["rules"]:
            self.stdout.write(
                f"{change['level']}: "
                f"{change['course_count']} verified courses — "
                f"{'update' if change['changed'] else 'unchanged'}"
            )

        self.stdout.write(f"Before/after report: {output}")
        self.stdout.write(
            "Rules updated. Tuition prices and stored discounts unchanged."
            if options["apply"]
            else "Preview only. No database records changed."
        )