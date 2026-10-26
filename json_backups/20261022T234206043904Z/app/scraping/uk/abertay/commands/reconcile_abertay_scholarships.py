import hashlib
import json
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    Scholarship,
    ScholarshipRule,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
)


SCHOLARSHIP_URL = (
    "https://www.abertay.ac.uk/study-apply/"
    "money-fees-and-funding/scholarships/"
    "abertay-international-scholarship/"
)

TERMS_URL = (
    "https://www.abertay.ac.uk/study-apply/"
    "money-fees-and-funding/terms-conditions/"
)

SCHOLARSHIP_FIELDS = (
    "award_value",
    "award_type",
    "currency",
    "deadline",
    "eligibility",
    "requirements",
    "description",
    "url",
)


def scholarship_snapshot(scholarship):
    """Capture the existing scholarship and calculation rule."""

    snapshot = {
        field: getattr(scholarship, field)
        for field in SCHOLARSHIP_FIELDS
    }

    snapshot["deadline"] = (
        snapshot["deadline"].isoformat()
        if snapshot["deadline"] else None
    )

    rule = ScholarshipRule.objects.filter(
        scholarship=scholarship
    ).first()

    snapshot["calculation_rule"] = (
        {
            "verified": rule.verified,
            "source_url": rule.source_url,
            "rules": rule.rules,
            "reviewed_at": (
                rule.reviewed_at.isoformat()
                if rule.reviewed_at else None
            ),
        }
        if rule else None
    )

    return snapshot


class Command(BaseCommand):
    help = (
        "Preview or apply reviewed Abertay annual-maximum "
        "scholarship information for 2026/27."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
        )

        parser.add_argument(
            "--link-reviewed-discounts",
            action="store_true",
            help=(
                "Also link the previewed stored GBP 3000 "
                "discounts after confirming their identity."
            ),
        )

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        apply = options["apply"]
        link_discounts = options["link_reviewed_discounts"]

        # Check that the installed calculator supports maximum awards.
        from app.scholarship_calculation import evaluate_award

        probe_rule = {
            "verified": True,
            "source_url": SCHOLARSHIP_URL,
            "rules": {
                "fee_basis_confirmed": True,
                "benefit": "tuition",
                "kind": "fixed_maximum",
                "value": "3000",
                "currency": "GBP",
                "academic_year": "2026/27",
                "study_years": [1],
                "conditions": {},
                "award_status": "conditional",
                "stacking": "exclusive",
                "compatible_ids": [],
                "percentage_basis": "original_tuition",
            },
        }

        candidate, error = evaluate_award(
            {"id": 0, "name": "Compatibility check", "deadline": None},
            probe_rule,
            {},
            Decimal("16950"),
            "GBP",
            "2026/27",
            1,
        )

        if not candidate or not candidate.get("is_maximum"):
            raise CommandError(
                "Update scholarship_calculation.py first."
            )

        try:
            evidence_bytes = (
                root / "abertay_scholarships_preview.json"
            ).read_bytes()

            evidence = json.loads(
                evidence_bytes.decode("utf-8-sig")
            )

            mapping = json.loads(
                (root / "abertay_course_mapping.json").read_text(
                    encoding="utf-8-sig"
                )
            )

        except (OSError, ValueError) as exc:
            raise CommandError(
                f"Cannot read scholarship evidence or mapping: {exc}"
            ) from exc

        if (
            evidence.get("source_url") != SCHOLARSHIP_URL
            or evidence.get("terms_source_url") != TERMS_URL
            or evidence.get("currency") != "GBP"
            or evidence.get("amount_type") != "annual_maximum"
        ):
            raise CommandError(
                "Unexpected scholarship source or amount type."
            )

        try:
            maximum = Decimal(str(evidence["annual_maximum"]))
        except (KeyError, ValueError, ArithmeticError) as exc:
            raise CommandError("Invalid annual maximum.") from exc

        if not maximum.is_finite() or maximum != Decimal("3000"):
            raise CommandError(
                "Published maximum changed; review before updating rules."
            )

        for key in ("overview", "eligibility", "application"):
            if not isinstance(evidence.get(key), list) or not evidence[key]:
                raise CommandError(f"Missing scholarship evidence: {key}")

        required_terms = (
            "Eligibility",
            "Acceptance",
            "Payment",
            "Multiple Awards",
            "Students from Partner institutions",
        )

        if not all(
            evidence.get("terms", {}).get(key)
            for key in required_terms
        ):
            raise CommandError("Scholarship terms are incomplete.")

        if mapping.get("academic_year") != "2026/27":
            raise CommandError("Expected mappings for 2026/27.")

        course_ids = sorted(
            entry["course_id"]
            for entry in mapping["mappings"]
            if entry.get("approved") is True
        )

        if (
            not course_ids
            or len(course_ids) != len(set(course_ids))
        ):
            raise CommandError(
                "Approved course mappings are missing or duplicated."
            )

        evidence_hash = hashlib.sha256(
            evidence_bytes
        ).hexdigest()

        report = {
            "mode": "apply" if apply else "preview",
            "academic_year": "2026/27",
            "evidence_sha256": evidence_hash,
            "link_reviewed_discounts": link_discounts,
            "scholarships": [],
            "tuition_links": [],
            "unlinked_discount_reviews": [],
            "status": "prepared",
        }

        log_directory = (
            root / "logs" / "scholarship-reconciliation"
        )
        log_directory.mkdir(parents=True, exist_ok=True)

        log_path = log_directory / (
            "abertay-"
            + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )

        description = (
            "Up to GBP 3,000 per academic year, for at most four "
            "years and no longer than the eligible course. "
            "This is a maximum, not a guaranteed award. "
            "Award notification and acceptance are required. "
            "Certain named scholarships may be combined under "
            "the university's rules; this calculation currently "
            "uses the base award alone. No verified application "
            "deadline is recorded from the inspected sources."
        )

        with transaction.atomic():
            scholarship_plans = {}

            for level in ("UG", "PG"):
                matches = list(
                    Scholarship.objects.select_for_update().filter(
                        university__name__iexact="Abertay University",
                        name__iexact="Abertay International Scholarship",
                        level=level,
                    )[:2]
                )

                if len(matches) != 1:
                    raise CommandError(
                        f"Expected one {level} scholarship; "
                        f"found {len(matches)}."
                    )

                scholarship = matches[0]
                before = scholarship_snapshot(scholarship)

                requirements = (
                    "New full-time entrant; international fee status; "
                    "academic offer conditions met; on-campus study "
                    "in Dundee; no partner tuition discount; no "
                    "repeat-year award. University award notification "
                    "and acceptance required."
                )

                if level == "PG":
                    requirements += (
                        " Postgraduate taught programme and UK 2:1 "
                        "equivalent or better required."
                    )

                proposed = {
                    "award_value": "3000.00",
                    "award_type": "Maximum annual tuition reduction",
                    "currency": "GBP",
                    "deadline": None,
                    "eligibility": (
                        "International (Overseas) fee status; "
                        "not nationality alone"
                    ),
                    "requirements": requirements,
                    "description": description,
                    "url": SCHOLARSHIP_URL,
                }

                conditions = {
                    "fee_status": ["INTL"],
                    "level": (
                        ["UG"] if level == "UG" else ["PG", "PGT"]
                    ),
                    "new_entrant": [True],
                    "full_time": [True],
                    "on_campus_dundee": [True],
                    "academic_conditions_met": [True],
                    "partner_discount": [False],
                    "repeat_year": [False],
                }

                if level == "UG":
                    conditions["course_id"] = course_ids
                else:
                    conditions.update({
                        "postgraduate_taught": [True],
                        "pg_2_1_equivalent_met": [True],
                    })

                rules = {
                    # PG fee bases have not yet been reviewed.
                    "fee_basis_confirmed": level == "UG",
                    "benefit": "tuition",
                    "kind": "fixed_maximum",
                    "value": "3000",
                    "currency": "GBP",
                    "academic_year": "2026/27",
                    # Enable the current first-year comparison only.
                    "study_years": [1],
                    "conditions": conditions,
                    "award_status": "conditional",
                    # Conservative base-only selection for now.
                    "stacking": "exclusive",
                    "compatible_ids": [],
                    "percentage_basis": "original_tuition",
                    "importer": "abertay-base-maximum-v1",
                    "terms_source_url": TERMS_URL,
                    "evidence_sha256": evidence_hash,
                    "published_maximum_years": 4,
                    "review_notes": (
                        "First compared study year only. Base award "
                        "only; named combinations are not implemented. "
                        "PG deductions remain blocked until PG "
                        "tuition bases are reviewed."
                    ),
                }

                proposed_rule = {
                    "verified": True,
                    "source_url": SCHOLARSHIP_URL,
                    "rules": rules,
                }

                previous_rule = before["calculation_rule"]

                changed = (
                    any(
                        before[key] != value
                        for key, value in proposed.items()
                    )
                    or not previous_rule
                    or any(
                        previous_rule[key] != value
                        for key, value in proposed_rule.items()
                    )
                )

                report["scholarships"].append({
                    "id": scholarship.pk,
                    "level": level,
                    "before": before,
                    "proposed": {
                        **proposed,
                        "calculation_rule": proposed_rule,
                    },
                    "changed": changed,
                })

                scholarship_plans[level] = {
                    "scholarship": scholarship,
                    "proposed": proposed,
                    "rule": proposed_rule,
                    "changed": changed,
                }

            tuitions = list(
                Tuition.objects.select_for_update().filter(
                    course_id__in=course_ids,
                    course__university__name__iexact="Abertay University",
                    course__level="UG",
                    residency="INTL",
                ).select_related("course")
            )

            by_course = {}
            for tuition in tuitions:
                by_course.setdefault(
                    tuition.course_id, []
                ).append(tuition)

            ug_scholarship_id = (
                scholarship_plans["UG"]["scholarship"].pk
            )

            for course_id in course_ids:
                matches = by_course.get(course_id, [])

                if len(matches) != 1:
                    raise CommandError(
                        f"{course_id}: expected one mapped "
                        "undergraduate international tuition."
                    )

                tuition = matches[0]

                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                if (
                    source is None
                    or source.academic_year != "2026/27"
                    or source.fee_basis != "annual_tuition"
                    or tuition.currency != "GBP"
                ):
                    raise CommandError(
                        f"{course_id}: reviewed annual fee "
                        "metadata is missing."
                    )

                if tuition.scholarship_source_id not in (
                    None,
                    ug_scholarship_id,
                ):
                    raise CommandError(
                        f"{course_id}: linked to a different scholarship."
                    )

                if (
                    tuition.scholarship_source_id is None
                    and tuition.scholarship not in (
                        Decimal("0"),
                        Decimal("3000"),
                    )
                ):
                    report["unlinked_discount_reviews"].append({
                        "course_id": course_id,
                        "amount": str(tuition.scholarship),
                        "reason": (
                            "Different stored discount preserved; "
                            "no automatic linking."
                        ),
                    })
                    continue

                if (
                    tuition.scholarship_source_id is None
                    and tuition.scholarship == Decimal("3000")
                ):
                    report["tuition_links"].append({
                        "tuition_id": tuition.pk,
                        "course_id": course_id,
                        "before": {
                            "scholarship": str(tuition.scholarship),
                            "scholarship_source_id": None,
                        },
                        "after": {
                            "scholarship": str(tuition.scholarship),
                            "scholarship_source_id": ug_scholarship_id,
                        },
                    })

            # Preserve the before/after plan before changing records.
            log_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            if apply:
                for plan in scholarship_plans.values():
                    if not plan["changed"]:
                        continue

                    scholarship = plan["scholarship"]

                    for key, value in plan["proposed"].items():
                        setattr(scholarship, key, value)

                    scholarship.save(
                        update_fields=list(plan["proposed"])
                    )

                    ScholarshipRule.objects.update_or_create(
                        scholarship=scholarship,
                        defaults={
                            **plan["rule"],
                            "reviewed_at": timezone.now(),
                        },
                    )

                links = (
                    {
                        item["tuition_id"]: item
                        for item in report["tuition_links"]
                    }
                    if link_discounts else {}
                )

                for tuition in tuitions:
                    if tuition.pk not in links:
                        continue

                    link = links[tuition.pk]

                    tuition.scholarship_source_id = (
                        link["after"]["scholarship_source_id"]
                    )
                    tuition.save(
                        update_fields=["scholarship_source"]
                    )

                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=tuition.course_id,
                        residency="INTL",
                        before=link["before"],
                        after={
                            **link["after"],
                            "action": "link_reviewed_annual_maximum",
                            "audit_file": str(log_path),
                        },
                    )

        report["status"] = (
            "committed" if apply else "preview_only"
        )

        log_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        changed_count = sum(
            item["changed"]
            for item in report["scholarships"]
        )

        self.stdout.write(
            f"Scholarship records to update: {changed_count}"
        )
        self.stdout.write(
            "Candidate stored GBP 3000 links for review: "
            f"{len(report['tuition_links'])}"
        )
        self.stdout.write(
            "Other stored discounts left for review: "
            f"{len(report['unlinked_discount_reviews'])}"
        )
        self.stdout.write(
            f"Before/after report: {log_path}"
        )

        if apply:
            self.stdout.write(
                "Applied. No course fields or tuition prices changed."
            )
            self.stdout.write(
                "Reviewed discount links saved."
                if link_discounts
                else "Stored discount links were not changed."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )