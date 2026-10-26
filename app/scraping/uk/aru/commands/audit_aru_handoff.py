import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from app.models import (
    University,
    Course,
    Tuition,
    TuitionSource,
    Scholarship,
    ScholarshipRule,
    DepositPolicy,
)


WORKED_UNIVERSITIES = ["UK001", "UK002", "UK003"]

SUPPORTED_BASES = {
    "annual_tuition",
    "first_year_tuition",
    "full_time_course_fee",
}


def same_amount(first, second):
    try:
        first = Decimal(str(first))
        second = Decimal(str(second))

        return (
            first.is_finite()
            and second.is_finite()
            and first > 0
            and first.quantize(Decimal("0.01"))
            == second.quantize(Decimal("0.01"))
        )
    except (ArithmeticError, ValueError, TypeError):
        return False


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(
            data,
            cls=DjangoJSONEncoder,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class Command(BaseCommand):
    help = (
        "Read-only ARU coverage audit and next UK university export."
    )

    def handle(self, *args, **options):
        university = University.objects.filter(
            university_id="UK003"
        ).first()

        if university is None:
            raise CommandError("ARU UK003 was not found.")

        courses = list(
            Course.objects.filter(
                university_id="UK003"
            )
            .order_by("course_id")
            .values()
        )

        tuitions = list(
            Tuition.objects.filter(
                course__university_id="UK003",
                residency="INTL",
            ).values()
        )

        sources = list(
            TuitionSource.objects.filter(
                tuition__course__university_id="UK003",
                tuition__residency="INTL",
            ).values()
        )

        rules = list(
            ScholarshipRule.objects.filter(
                scholarship__university_id="UK003"
            ).values(
                "id",
                "scholarship_id",
                "scholarship__name",
                "verified",
                "source_url",
                "rules",
            )
        )

        policies = list(
            DepositPolicy.objects.filter(
                university_id="UK003"
            ).values()
        )

        tuition_by_course = defaultdict(list)

        for tuition in tuitions:
            tuition_by_course[tuition["course_id"]].append(tuition)

        source_by_tuition = {
            source["tuition_id"]: source
            for source in sources
        }

        fee_counts = Counter()
        fee_basis_counts = Counter()
        fee_year_counts = Counter()
        course_results = []

        for course in courses:
            if not course["is_active"]:
                continue

            course_id = course["course_id"]
            rows = tuition_by_course[course_id]

            source = None
            raw = {}
            fee_status = "pending"
            reason = ""

            if len(rows) != 1:
                reason = (
                    "Expected one international tuition; "
                    f"found {len(rows)}."
                )
            else:
                tuition = rows[0]
                source = source_by_tuition.get(tuition["id"])

                if source and isinstance(source["raw_fees"], dict):
                    raw = source["raw_fees"]

                if (
                    raw.get("international_applicants_eligible")
                    is False
                    or raw.get("required_fee_status") == "HOME"
                ):
                    fee_status = "restricted"
                    reason = (
                        raw.get("verification_reason")
                        or raw.get("fee_note")
                        or "International comparison restricted."
                    )

                elif (
                    source
                    and raw.get("verified") is True
                    and tuition["currency"] == "GBP"
                    and raw.get("currency") == "GBP"
                    and source["academic_year"]
                    and raw.get("academic_year")
                    == source["academic_year"]
                    and source["source_url"]
                    and source["fee_basis"] in SUPPORTED_BASES
                    and raw.get("source_fee_category")
                    == "International"
                    and same_amount(
                        tuition["tuition_fee"],
                        raw.get("annual_tuition"),
                    )
                ):
                    fee_status = "verified"

                else:
                    reason = (
                        raw.get("verification_reason")
                        or raw.get("fee_note")
                        or "Applicable international fee/source "
                        "has not been verified."
                    )

            year = source["academic_year"] if source else ""
            basis = source["fee_basis"] if source else ""

            matching_rules = []

            if fee_status == "verified":
                for rule in rules:
                    data = rule["rules"]

                    if (
                        not rule["verified"]
                        or not rule["source_url"]
                        or not isinstance(data, dict)
                        or data.get("academic_year") != year
                        or data.get("currency") != "GBP"
                        or data.get("benefit") != "tuition"
                        or data.get("fee_basis_confirmed") is not True
                        or data.get("kind")
                        not in {"fixed", "fixed_maximum", "percentage"}
                    ):
                        continue

                    conditions = data.get("conditions", {})

                    if not isinstance(conditions, dict):
                        continue

                    scope = conditions.get("course_id", [])

                    if (
                        isinstance(scope, list)
                        and course_id in scope
                        and 1 in data.get("study_years", [])
                    ):
                        matching_rules.append({
                            "id": rule["id"],
                            "name": rule["scholarship__name"],
                            "kind": data.get("kind"),
                            "value": data.get("value"),
                            "conditions": conditions,
                            "eligibility_questions": data.get(
                                "eligibility_questions", []
                            ),
                        })

            matching_policies = []

            if fee_status == "verified":
                for policy in policies:
                    data = policy["rules"]

                    if (
                        not policy["verified"]
                        or not policy["source_url"]
                        or policy["academic_year"] != year
                        or policy["currency"] != "GBP"
                        or not isinstance(data, dict)
                    ):
                        continue

                    scope = data.get("course_ids", [])

                    if (
                        isinstance(scope, list)
                        and course_id in scope
                        and raw.get("course_kind")
                        in data.get("course_kinds", [])
                    ):
                        matching_policies.append({
                            "id": policy["id"],
                            "academic_year": policy["academic_year"],
                            "kind": data.get("kind"),
                            "amount": data.get("amount"),
                            "eligibility_questions": data.get(
                                "eligibility_questions", []
                            ),
                            "note": data.get("note"),
                        })

            fee_counts[fee_status] += 1

            if fee_status == "verified":
                fee_basis_counts[basis] += 1
                fee_year_counts[year] += 1

            reviews = {
                key: raw[key]
                for key in [
                    "scholarship_review",
                    "deposit_review",
                    "award_review",
                    "award_review_note",
                    "scholarship_status",
                    "deposit_status",
                    "review_reason_code",
                ]
                if key in raw
            }

            course_results.append({
                "course_id": course_id,
                "title": course["title"],
                "level": course["level"],
                "location": course["location"],
                "intake": (
                    f"{course['start_month']} "
                    f"{course['start_year']}"
                ),
                "academic_year": year,
                "fee_basis": basis,
                "fee_status": fee_status,
                "fee_review_reason": reason,
                "conditional_scholarship_rules": matching_rules,
                "applicable_deposit_policies": matching_policies,
                "saved_reviews": reviews,
            })

        verified = [
            row
            for row in course_results
            if row["fee_status"] == "verified"
        ]

        scholarship_gaps = [
            row
            for row in verified
            if not row["conditional_scholarship_rules"]
        ]

        deposit_gaps = [
            row
            for row in verified
            if not row["applicable_deposit_policies"]
        ]

        pending = [
            row
            for row in course_results
            if row["fee_status"] == "pending"
        ]

        restricted = [
            row
            for row in course_results
            if row["fee_status"] == "restricted"
        ]

        summary = {
            "total_course_records": len(courses),
            "active_courses": len(course_results),
            "inactive_courses": sum(
                not course["is_active"] for course in courses
            ),
            "verified_international_fees": fee_counts["verified"],
            "pending_international_fees": fee_counts["pending"],
            "restricted_international_comparisons": (
                fee_counts["restricted"]
            ),
            "verified_fee_bases": dict(fee_basis_counts),
            "verified_fee_years": dict(fee_year_counts),
            "scholarship_rule_records": len(rules),
            "verified_scholarship_rule_records": sum(
                bool(rule["verified"]) for rule in rules
            ),
            "unverified_scholarship_rule_records": sum(
                not rule["verified"] for rule in rules
            ),
            "verified_fee_courses_with_conditional_award_scope": sum(
                bool(row["conditional_scholarship_rules"])
                for row in verified
            ),
            "verified_fee_courses_without_conditional_award_scope": (
                len(scholarship_gaps)
            ),
            "deposit_policy_records": len(policies),
            "verified_deposit_policy_records": sum(
                bool(policy["verified"]) for policy in policies
            ),
            "verified_fee_courses_with_deposit_scope": sum(
                bool(row["applicable_deposit_policies"])
                for row in verified
            ),
            "verified_fee_courses_without_deposit_scope": (
                len(deposit_gaps)
            ),
        }

        inventory = list(
            University.objects.filter(country="UK")
            .order_by("university_id")
            .values("university_id", "name", "slug")
        )

        next_university = (
            University.objects.filter(country="UK")
            .exclude(university_id__in=WORKED_UNIVERSITIES)
            .order_by("university_id")
            .first()
        )

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")

        folder = (
            Path(settings.BASE_DIR)
            / "logs"
            / "scraping-progress"
            / f"aru-handoff-{stamp}"
        )

        report = {
            "generated_at": timezone.now().isoformat(),
            "database_changes": False,
            "university_id": "UK003",
            "university_name": university.name,
            "status": "reviewed_with_outstanding_cases",
            "summary": summary,
            "pending_fee_cases": pending,
            "restricted_cases": restricted,
            "verified_fee_scholarship_scope_gaps": scholarship_gaps,
            "verified_fee_deposit_scope_gaps": deposit_gaps,
            "course_results": course_results,
            "scholarship_rules": rules,
            "deposit_policies": policies,
            "uk_inventory": inventory,
            "worked_university_ids": WORKED_UNIVERSITIES,
            "not_started_in_current_uk_workflow": len([
                row for row in inventory
                if row["university_id"] not in WORKED_UNIVERSITIES
            ]),
            "counting_notes": [
                "Scholarship scope is conditional, not a student award.",
                "Missing scholarship scope does not prove no funding exists.",
                "Missing deposit scope does not mean no deposit is required.",
                "Verified fee bases are counted separately.",
                "Pending review metadata is not fee verification.",
                "No synthetic student eligibility answers are applied.",
            ],
        }

        audit_path = folder / "aru-final-audit.json"
        write_json(audit_path, report)

        self.stdout.write(
            json.dumps(summary, indent=2)
        )
        self.stdout.write(f"\nARU audit: {audit_path}")

        if next_university is not None:
            next_id = next_university.university_id

            next_data = {
                "generated_at": timezone.now().isoformat(),
                "database_changes": False,
                "university_id": next_id,
                "university": (
                    University.objects.filter(
                        university_id=next_id
                    ).values().get()
                ),
                "courses": list(
                    Course.objects.filter(
                        university_id=next_id
                    ).order_by("course_id").values()
                ),
                "tuitions": list(
                    Tuition.objects.filter(
                        course__university_id=next_id
                    ).values()
                ),
                "sources": list(
                    TuitionSource.objects.filter(
                        tuition__course__university_id=next_id
                    ).values()
                ),
                "scholarships": list(
                    Scholarship.objects.filter(
                        university_id=next_id
                    ).values()
                ),
                "scholarship_rules": list(
                    ScholarshipRule.objects.filter(
                        scholarship__university_id=next_id
                    ).values()
                ),
                "deposit_policies": list(
                    DepositPolicy.objects.filter(
                        university_id=next_id
                    ).values()
                ),
            }

            next_path = folder / "next-uk-university-export.json"
            write_json(next_path, next_data)

            self.stdout.write(
                f"\nNext university: {next_id} "
                f"{next_university.name}"
            )
            self.stdout.write(
                f"Courses: {len(next_data['courses'])}"
            )
            self.stdout.write(
                f"Scholarships: {len(next_data['scholarships'])}"
            )
            self.stdout.write(
                f"Deposit policies: "
                f"{len(next_data['deposit_policies'])}"
            )
            self.stdout.write(
                f"Next university export: {next_path}"
            )

        else:
            self.stdout.write(
                "No unstarted UK university found."
            )

        self.stdout.write(
            "\nAudit and export complete. "
            "No database records changed."
        )