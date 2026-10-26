import copy
import json
import re
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    ScholarshipRule,
    DepositPolicy,
)
from app.scraping.uk.aru.commands.import_aru_embedded_postgraduate_fees import extract_configuration
from app.scraping.uk.aru.commands.import_aru_corrected_undergraduate_fees import standard_fee_evidence


YEAR = "2027/28"

BATCH = [
    {
        "id": "C_UK003_20",
        "title": "MSc International Business",
        "heading": "International Business",
        "level": "PG",
        "duration": "1 Year",
        "campus": "Cambridge",
        "url": (
            "https://www.aru.ac.uk/study/postgraduate/"
            "international-business"
        ),
        "fee": "20925.00",
        "basis": "annual_tuition",
    },
    {
        "id": "C_UK003_27",
        "title": "MA Children's Book Illustration",
        "heading": "MA Children's Book Illustration",
        "level": "PG",
        "duration": "1.5 Years",
        "campus": "Cambridge",
        "url": (
            "https://www.aru.ac.uk/study/postgraduate/"
            "childrens-book-illustration"
        ),
        "fee": "12450.00",
        "basis": "first_year_tuition",
    },
    {
        "id": "C_UK003_40",
        "title": "BSc (Hons) Zoology",
        "heading": "Zoology",
        "level": "UG",
        "duration": "3 Years",
        "campus": "Cambridge",
        "url": (
            "https://www.aru.ac.uk/study/undergraduate/zoology"
        ),
        "fee": "19750.00",
        "basis": "annual_tuition",
    },
]

COURSE_FIELDS = (
    "title", "level", "duration", "location",
    "start_month", "start_year", "course_url",
)
SOURCE_FIELDS = (
    "academic_year", "source_url", "source_course_code",
    "source_course_name", "fee_basis", "raw_fees",
)


def snapshot(obj, fields):
    if obj is None:
        return None
    return {
        key: copy.deepcopy(getattr(obj, key))
        for key in fields
    }


class Command(BaseCommand):
    help = (
        "Reconcile three fee-ready ARU records from the "
        "first 30-course review, including saved-rule scopes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        logs = Path(settings.BASE_DIR) / "logs" / "aru"
        files = list(
            logs.glob("remaining-evidence-*/remaining-evidence.json")
        )
        if not files:
            raise CommandError(
                "Run inspect_aru_remaining_evidence first."
            )

        evidence_path = max(
            files, key=lambda path: path.parent.name
        )

        try:
            evidence = json.loads(
                evidence_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        if evidence.get("university_id") != "UK003":
            raise CommandError("Evidence is not for UK003.")

        pages = {}
        for page in evidence.get("pages", []):
            key = page.get("requested_url", "").rstrip("/")
            if key in pages:
                raise CommandError(f"Duplicate evidence: {key}")
            pages[key] = page

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        output = logs / f"review-batch-one-{stamp}.json"

        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
            "evidence_report": str(evidence_path.resolve()),
            "records": [],
            "scope_changes": [],
            "blocked": [],
        }

        def save_report():
            output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        with transaction.atomic():
            pending = []

            for row in BATCH:
                try:
                    page = pages.get(row["url"].rstrip("/"))
                    if (
                        page is None
                        or page.get("source_url", "").rstrip("/")
                        != row["url"].rstrip("/")
                        or page.get("title") != row["heading"]
                        or not page.get("sha256")
                        or row["id"] not in page.get(
                            "candidate_course_ids", []
                        )
                    ):
                        raise ValueError(
                            "Course-page identity needs review."
                        )

                    body = page.get("text", "").split(
                        "End of main navigation."
                    )[-1]
                    amount = Decimal(row["fee"])
                    fee_details = {}

                    if row["id"] == "C_UK003_27":
                        configuration = extract_configuration(page)

                        matching_options = [
                            option
                            for option in configuration.get(
                                "CourseOptions", []
                            )
                            if option.get("Duration", {}).get("Name")
                            == "18 months full-time"
                            and "September 2027" in [
                                item.get("Name")
                                for item in option.get("StartDate", [])
                            ]
                            and "Cambridge" in [
                                item.get("ShortName")
                                for item in option.get("Location", [])
                            ]
                        ]

                        if len(matching_options) != 1:
                            raise ValueError(
                                "Unique 18-month September 2027 "
                                "Cambridge route not confirmed."
                            )

                        selected = matching_options[0]
                        fees = [
                            fee
                            for fee in selected.get("TuitionFees", [])
                            if fee.get("FeeBand", {}).get("Description")
                            == (
                                "International students starting "
                                "2027/28 (total cost)"
                            )
                        ]

                        if len(fees) != 1:
                            raise ValueError(
                                "Unique international total fee "
                                "not confirmed."
                            )

                        selected_fee = fees[0]
                        total = Decimal(str(
                            selected_fee["FeeBand"]["Value"]
                        ))
                        if total != Decimal("24900.00"):
                            raise ValueError(
                                "Published programme total differs."
                            )

                        information = " ".join(
                            BeautifulSoup(
                                item.get("RichTextContent", ""),
                                "html.parser",
                            ).get_text(" ", strip=True)
                            for item in selected_fee.get(
                                "FeesInformation", []
                            )
                        )

                        if not re.search(
                            r"£12,450\s+in Year 1\s+and\s+"
                            r"£12,450\s+in Year 2",
                            information,
                        ):
                            raise ValueError(
                                "The explicit first/second-year "
                                "schedule needs review."
                            )

                        fee_details = {
                            "programme_total": "24900.00",
                            "first_year_tuition": "12450.00",
                            "published_yearly_schedule": {
                                "1": "12450.00",
                                "2": "12450.00",
                            },
                            "selected_fee": selected_fee,
                            "schedule_evidence": information,
                        }

                        note = (
                            "First-year tuition: £12,450. Published "
                            "total for the 18-month full-time course: "
                            "£24,900, paid as £12,450 in Year 1 and "
                            "£12,450 in Year 2. The compared amount "
                            "is not the whole-course total."
                        )

                    else:
                        if row["level"] == "PG":
                            route = re.search(
                                r"Start date\s+(.*?)\s+Location\s+(.*?)"
                                r"\s+Award\s+MSc\s+Duration"
                                r"\s+1 year full-time\b",
                                body,
                            )
                        else:
                            route = re.search(
                                r"Start date\s+(.*?)\s+Location\s+(.*?)"
                                r"\s+UCAS course code\s+C300"
                                r"\s+Duration\s+3 years\b",
                                body,
                            )

                        if (
                            route is None
                            or "September 2027" not in route.group(1)
                            or row["campus"]
                            not in route.group(2).split()
                        ):
                            raise ValueError(
                                "Standard September 2027 campus "
                                "route not confirmed."
                            )

                        if re.search(
                            r"start dates?\s+(?:may\s+)?vary",
                            body[:route.end() + 250],
                            flags=re.IGNORECASE,
                        ):
                            raise ValueError(
                                "Campus-specific intake "
                                "requires confirmation."
                            )

                        excerpt = standard_fee_evidence(body)
                        amounts = {
                            Decimal(value.replace(",", ""))
                            for value in re.findall(
                                r"£\s*([\d,]+(?:\.\d{2})?)"
                                r"\s+International students starting"
                                r"\s+2027/28"
                                r"\s+\(full-time,\s*per year\)",
                                excerpt,
                            )
                        }

                        if amounts != {amount}:
                            raise ValueError(
                                "Standard international annual "
                                "fee differs or is ambiguous."
                            )

                        fee_details["standard_route_fee_evidence"] = excerpt
                        note = (
                            "Published annual international tuition "
                            "for the standard September 2027 "
                            f"full-time route at {row['campus']}. "
                            "Optional placement routes are excluded."
                        )

                    course = (
                        Course.objects.select_for_update()
                        .get(
                            course_id=row["id"],
                            university_id="UK003",
                        )
                    )

                    if (
                        course.title != row["title"]
                        or course.level != row["level"]
                        or course.duration != row["duration"]
                        or course.location != f"{row['campus']} Campus"
                        or not course.is_active
                    ):
                        raise ValueError(
                            "Stored course identity needs review."
                        )

                    rows = list(
                        Tuition.objects.select_for_update().filter(
                            course=course,
                            residency="INTL",
                        )
                    )
                    if (
                        len(rows) != 1
                        or rows[0].currency != "GBP"
                    ):
                        raise ValueError(
                            "Exactly one GBP international "
                            "tuition is required."
                        )

                    tuition = rows[0]
                    source = (
                        TuitionSource.objects.select_for_update()
                        .filter(tuition=tuition).first()
                    )

                    before = {
                        "course": snapshot(course, COURSE_FIELDS),
                        "tuition_fee": format(
                            Decimal(str(tuition.tuition_fee)), ".2f"
                        ),
                        "scholarship": format(
                            Decimal(str(tuition.scholarship)), ".2f"
                        ),
                        "scholarship_source_id": (
                            tuition.scholarship_source_id
                        ),
                        "source": snapshot(source, SOURCE_FIELDS),
                    }

                    raw = {
                        "verified": True,
                        "academic_year": YEAR,
                        "source_fee_category": "International",
                        "course_kind": (
                            "undergraduate_taught"
                            if row["level"] == "UG"
                            else "postgraduate_taught"
                        ),
                        "study_year": 1,
                        "study_mode": "full_time",
                        # Compatibility amount for the existing
                        # calculator; fee_basis identifies Year 1.
                        "annual_tuition": str(amount),
                        "currency": "GBP",
                        "course_source_url": row["url"],
                        "source_sha256": page["sha256"],
                        "fee_method": (
                            "published_year_one_schedule"
                            if row["basis"] == "first_year_tuition"
                            else "reviewed_standard_route_fee"
                        ),
                        "fee_note": note,
                        "unverified_legacy_discount_disabled": True,
                        "scholarship_status": (
                            "verified_rule_eligibility_required"
                            if row["level"] == "UG"
                            else "verified_maximum_rule_eligibility_required"
                        ),
                        "deposit_status": (
                            "verified_current_policy_offer_confirmation_required"
                        ),
                        "additional_awards_status": (
                            "Course-specific bursaries require "
                            "separate review; no extra deduction."
                        ),
                        "fee_categories": [{
                            "source_fee_category": "International",
                            "academic_year": YEAR,
                            "currency": "GBP",
                            "annual_tuition": str(amount),
                            "study_year": 1,
                            "amount_requires_review": False,
                        }],
                        **fee_details,
                    }

                    after = {
                        "course": {
                            **before["course"],
                            "start_month": "September",
                            "start_year": 2027,
                            "course_url": row["url"],
                        },
                        "tuition_fee": str(amount),
                        "scholarship": "0.00",
                        "scholarship_source_id": None,
                        "source": {
                            "academic_year": YEAR,
                            "source_url": row["url"],
                            "source_course_code": (
                                "C300" if row["level"] == "UG" else ""
                            ),
                            "source_course_name": row["title"],
                            "fee_basis": row["basis"],
                            "raw_fees": raw,
                        },
                    }

                    record = {
                        "course_id": row["id"],
                        "changed": before != after,
                        "before": before,
                        "after": after,
                    }
                    report["records"].append(record)
                    pending.append((course, tuition, record))

                except (ValueError, KeyError, Course.DoesNotExist) as exc:
                    report["blocked"].append({
                        "course_id": row["id"],
                        "reason": str(exc),
                    })

            if report["blocked"]:
                save_report()
                raise CommandError(
                    f"No changes applied. Review: {output}"
                )

            scope_specs = [
                (
                    "ARU International Undergraduate Scholarship "
                    f"— Lower — Year 1 ({YEAR})",
                    "UG", "fixed", "2000.00", ["C_UK003_40"],
                ),
                (
                    "ARU International Undergraduate Scholarship "
                    f"— Higher — Year 1 ({YEAR})",
                    "UG", "fixed", "4000.00", ["C_UK003_40"],
                ),
                (
                    "ARU International Postgraduate Scholarship "
                    f"— ARU graduate eligibility ({YEAR})",
                    "PG", "fixed_maximum", "4000.00",
                    ["C_UK003_20", "C_UK003_27"],
                ),
            ]

            pending_rules = []

            for name, level, kind, value, additions in scope_specs:
                matches = list(
                    ScholarshipRule.objects.select_for_update().filter(
                        scholarship__university_id="UK003",
                        scholarship__name=name,
                        scholarship__level=level,
                        verified=True,
                    )
                )
                if len(matches) != 1:
                    raise CommandError(
                        f"One verified saved rule is required: {name}"
                    )

                saved = matches[0]
                old = copy.deepcopy(saved.rules)
                conditions = (
                    old.get("conditions")
                    if isinstance(old, dict) else None
                )
                if (
                    not isinstance(conditions, dict)
                    or old.get("academic_year") != YEAR
                    or old.get("kind") != kind
                    or old.get("value") != value
                    or old.get("study_years") != [1]
                    or not isinstance(conditions.get("course_id"), list)
                    or not old.get("eligibility_questions")
                ):
                    raise CommandError(
                        f"Saved rule needs review: {name}"
                    )

                new = copy.deepcopy(old)
                new["conditions"]["course_id"] = sorted(
                    set(conditions["course_id"]) | set(additions)
                )

                report["scope_changes"].append({
                    "name": name,
                    "changed": old != new,
                    "before": old,
                    "after": new,
                })
                pending_rules.append((saved, old, new))

            policy = (
                DepositPolicy.objects.select_for_update().filter(
                    university_id="UK003",
                    academic_year=YEAR,
                    currency="GBP",
                    verified=True,
                ).first()
            )
            old_policy = copy.deepcopy(policy.rules) if policy else None

            if (
                not isinstance(old_policy, dict)
                or old_policy.get("kind") != "fixed"
                or old_policy.get("amount") != "4000.00"
                or old_policy.get("academic_year") != YEAR
                or not isinstance(old_policy.get("course_ids"), list)
            ):
                raise CommandError(
                    "A valid saved ARU deposit policy is required."
                )

            new_policy = copy.deepcopy(old_policy)
            new_policy["course_ids"] = sorted(
                set(old_policy["course_ids"])
                | {row["id"] for row in BATCH}
            )

            report["deposit_scope"] = {
                "changed": old_policy != new_policy,
                "before": old_policy,
                "after": new_policy,
            }

            if options["apply"]:
                for course, tuition, record in pending:
                    if not record["changed"]:
                        continue

                    after = record["after"]
                    fields = []
                    for key, value in after["course"].items():
                        if getattr(course, key) != value:
                            setattr(course, key, value)
                            fields.append(key)
                    if fields:
                        course.save(
                            update_fields=[*fields, "updated_at"]
                        )

                    tuition.tuition_fee = Decimal(after["tuition_fee"])
                    tuition.scholarship = 0
                    tuition.scholarship_source = None
                    tuition.save(update_fields=[
                        "tuition_fee", "scholarship", "scholarship_source"
                    ])

                    TuitionSource.objects.update_or_create(
                        tuition=tuition,
                        defaults=after["source"],
                    )
                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=course.course_id,
                        residency="INTL",
                        before=record["before"],
                        after=after,
                    )

                for saved, old, new in pending_rules:
                    if old != new:
                        saved.rules = new
                        saved.reviewed_at = timezone.now()
                        saved.save(update_fields=[
                            "rules", "reviewed_at"
                        ])

                if old_policy != new_policy:
                    policy.rules = new_policy
                    policy.save(update_fields=["rules", "updated_at"])

                report["database_changes"] = (
                    any(r["changed"] for r in report["records"])
                    or any(r["changed"] for r in report["scope_changes"])
                    or old_policy != new_policy
                )

        save_report()

        for record in report["records"]:
            self.stdout.write(
                f"{record['course_id']}: GBP "
                f"{record['before']['tuition_fee']} -> "
                f"{record['after']['tuition_fee']}; "
                f"{record['after']['source']['fee_basis']} — "
                f"{'update' if record['changed'] else 'unchanged'}"
            )

        changes = (
            sum(r["changed"] for r in report["records"])
            + sum(r["changed"] for r in report["scope_changes"])
            + int(old_policy != new_policy)
        )
        self.stdout.write(
            f"\nDeposit scope: {len(new_policy['course_ids'])} courses"
        )
        self.stdout.write(f"Proposed/updated items: {changes}")
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Reviewed fees, intake and rule scopes saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )