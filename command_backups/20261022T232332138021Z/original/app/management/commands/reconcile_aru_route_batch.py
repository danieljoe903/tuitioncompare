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
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    ScholarshipRule,
    DepositPolicy,
)


YEAR = "2027/28"

BATCH = [
    {
        "id": "C_ARU003_02",
        "url": (
            "https://www.aru.ac.uk/study/undergraduate/"
            "international-nursing-studies"
        ),
        "heading": "International Nursing Studies (Top-Up)",
        "old_title": "BSc (Hons) International Nursing (Top-up)",
        "title": "BSc (Hons) International Nursing Studies (Top-Up)",
        "level": "TOPUP",
        "campus": "Chelmsford",
        "old_duration": "1 Year",
        "duration": "1 Year",
        "award": "BSc (Hons)",
        "ucas": "B700",
        "fee": "18825.00",
    },
    {
        "id": "C_UK003_55",
        "url": (
            "https://www.aru.ac.uk/study/postgraduate/"
            "animal-welfare-and-conservation"
        ),
        "heading": "Animal Welfare and Conservation",
        "old_title": "MSc Animal Welfare and Conservation",
        "title": "MSc Animal Welfare and Conservation",
        "level": "PG",
        "campus": "Writtle",
        "old_duration": "1.3 Years",
        "duration": "1 Year",
        "award": "MSc",
        "ucas": "",
        "fee": "18825.00",
    },
    {
        "id": "C_UK003_76",
        "url": (
            "https://www.aru.ac.uk/study/postgraduate/"
            "applied-data-science"
        ),
        "heading": "Applied Data Science (Conversion)",
        "old_title": "MSc Applied Data Science",
        "title": "MSc Applied Data Science (Conversion)",
        "level": "PG",
        "campus": "Cambridge",
        "old_duration": "1 Year",
        "duration": "1 Year",
        "award": "MSc",
        "ucas": "",
        "fee": "20925.00",
    },
]

IDS = [row["id"] for row in BATCH]
PG_IDS = [row["id"] for row in BATCH if row["level"] == "PG"]

COURSE_FIELDS = (
    "title", "level", "duration", "location",
    "start_month", "start_year", "course_url",
    "ucas_cao_code",
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
        "Preview or apply three reviewed ARU route corrections, "
        "annual fees and applicable saved-rule scope extensions."
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
        output = logs / f"route-batch-{stamp}.json"
        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
            "evidence_report": str(evidence_path.resolve()),
            "records": [],
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
                course_id = row["id"]

                try:
                    page = pages.get(row["url"].rstrip("/"))
                    if (
                        page is None
                        or page.get("source_url", "").rstrip("/")
                        != row["url"].rstrip("/")
                        or page.get("title") != row["heading"]
                        or not page.get("sha256")
                        or course_id not in page.get(
                            "candidate_course_ids", []
                        )
                    ):
                        raise ValueError(
                            "Course-page identity needs review."
                        )

                    body = page.get("text", "").split(
                        "End of main navigation."
                    )[-1]

                    if row["level"] == "TOPUP":
                        pattern = (
                            r"Start date\s+(.*?)\s+Location\s+(.*?)"
                            r"\s+UCAS course code\s+(.*?)"
                            r"\s+Duration\s+1 year\b"
                        )
                    else:
                        pattern = (
                            r"Start date\s+(.*?)\s+Location\s+(.*?)"
                            r"\s+Award\s+(.*?)"
                            r"\s+Duration\s+1 year full-time\b"
                        )

                    route = re.search(pattern, body)

                    if (
                        route is None
                        or "September 2027" not in route.group(1)
                        or route.group(2).strip() != row["campus"]
                    ):
                        raise ValueError(
                            "September 2027 one-year campus route "
                            "is not confirmed."
                        )

                    expected_route_value = (
                        row["ucas"]
                        if row["level"] == "TOPUP"
                        else row["award"]
                    )
                    if route.group(3).strip() != expected_route_value:
                        raise ValueError(
                            "Published award or UCAS code differs."
                        )

                    expected_prefix = (
                        "Undergraduate BSc (Hons)"
                        if row["level"] == "TOPUP"
                        else "Postgraduate MSc"
                    )
                    if not body.strip().startswith(expected_prefix):
                        raise ValueError("Published award differs.")

                    marker = body.rfind("Fees and funding")
                    if marker < 0:
                        raise ValueError("Fee section is missing.")

                    amounts = {
                        Decimal(value.replace(",", ""))
                        for value in re.findall(
                            r"£\s*([\d,]+(?:\.\d{2})?)"
                            r"\s+International students starting"
                            r"\s+2027/28"
                            r"\s+\(full-time,\s*per year\)",
                            body[marker:],
                        )
                    }
                    amount = Decimal(row["fee"])

                    if amounts != {amount}:
                        raise ValueError(
                            f"Annual international fee differs: "
                            f"{sorted(amounts)}."
                        )

                    course = (
                        Course.objects.select_for_update()
                        .get(
                            course_id=course_id,
                            university_id="UK003",
                        )
                    )

                    if (
                        not course.is_active
                        or course.title not in {
                            row["old_title"], row["title"]
                        }
                        or course.duration not in {
                            row["old_duration"], row["duration"]
                        }
                        or course.level != row["level"]
                        or course.location
                        != f"{row['campus']} Campus"
                    ):
                        raise ValueError(
                            "Stored course identity needs review."
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
                        raise ValueError(
                            "Exactly one GBP international "
                            "tuition is required."
                        )

                    tuition = tuitions[0]
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

                    after_course = {
                        **before["course"],
                        "title": row["title"],
                        "duration": row["duration"],
                        "start_month": "September",
                        "start_year": 2027,
                        "course_url": row["url"],
                    }
                    if row["ucas"]:
                        after_course["ucas_cao_code"] = row["ucas"]

                    old_raw = (
                        source.raw_fees
                        if source and isinstance(source.raw_fees, dict)
                        else {}
                    )
                    history = copy.deepcopy(
                        old_raw.get("reviewed_course_corrections", {})
                    )
                    for field in ("title", "duration"):
                        if before["course"][field] != after_course[field]:
                            history.setdefault(field, {
                                "before": before["course"][field],
                                "after": after_course[field],
                            })

                    raw = {
                        "verified": True,
                        "academic_year": YEAR,
                        "source_fee_category": "International",
                        "course_kind": (
                            "undergraduate_topup"
                            if row["level"] == "TOPUP"
                            else "postgraduate_taught"
                        ),
                        "annual_tuition": str(amount),
                        "currency": "GBP",
                        "study_year": 1,
                        "study_mode": "full_time",
                        "campus": row["campus"],
                        "course_source_url": row["url"],
                        "source_sha256": page["sha256"],
                        "fee_method": "reviewed_named_course_fee",
                        "reviewed_course_corrections": history,
                        "unverified_legacy_discount_disabled": True,
                        "scholarship_status": (
                            "pending_topup_entry_year_confirmation"
                            if row["level"] == "TOPUP"
                            else "verified_maximum_rule_eligibility_required"
                        ),
                        "deposit_status": (
                            "verified_current_policy_offer_confirmation_required"
                        ),
                        "fee_note": (
                            "Published annual international fee for "
                            "the September 2027 one-year full-time "
                            f"route at {row['campus']}."
                        ),
                        "fee_categories": [{
                            "source_fee_category": "International",
                            "academic_year": YEAR,
                            "currency": "GBP",
                            "annual_tuition": str(amount),
                            "amount_requires_review": False,
                        }],
                    }

                    if row["level"] == "TOPUP":
                        raw["course_note"] = (
                            "For qualified, experienced international "
                            "nurses. This course does not lead to "
                            "Nursing and Midwifery Council registration. "
                            "Scholarship entry-year treatment remains "
                            "unconfirmed."
                        )

                    after = {
                        "course": after_course,
                        "tuition_fee": str(amount),
                        "scholarship": "0.00",
                        "scholarship_source_id": None,
                        "source": {
                            "academic_year": YEAR,
                            "source_url": row["url"],
                            "source_course_code": row["ucas"],
                            "source_course_name": row["title"],
                            "fee_basis": "annual_tuition",
                            "raw_fees": raw,
                        },
                    }

                    record = {
                        "course_id": course_id,
                        "changed": before != after,
                        "before": before,
                        "after": after,
                    }
                    report["records"].append(record)
                    pending.append((course, tuition, record))

                except (ValueError, Course.DoesNotExist) as exc:
                    report["blocked"].append({
                        "course_id": course_id,
                        "reason": str(exc),
                    })

            if report["blocked"]:
                save_report()
                raise CommandError(
                    "No changes applied. "
                    f"Review blocked records in {output}"
                )

            rule_name = (
                "ARU International Postgraduate Scholarship "
                f"— ARU graduate eligibility ({YEAR})"
            )

            rules = list(
                ScholarshipRule.objects.select_for_update().filter(
                    scholarship__university_id="UK003",
                    scholarship__name=rule_name,
                    scholarship__level="PG",
                    verified=True,
                )
            )
            if len(rules) != 1:
                raise CommandError(
                    "Exactly one verified ARU graduate rule is required."
                )

            rule = rules[0]
            old_rule = copy.deepcopy(rule.rules)
            conditions = (
                old_rule.get("conditions")
                if isinstance(old_rule, dict) else None
            )

            if (
                not isinstance(conditions, dict)
                or old_rule.get("academic_year") != YEAR
                or old_rule.get("kind") != "fixed_maximum"
                or old_rule.get("value") != "4000.00"
                or conditions.get("aru_graduate") != [True]
                or not isinstance(conditions.get("course_id"), list)
            ):
                raise CommandError(
                    "The saved graduate scholarship rule needs review."
                )

            new_rule = copy.deepcopy(old_rule)
            new_rule["conditions"]["course_id"] = sorted(
                set(conditions["course_id"]) | set(PG_IDS)
            )

            policy = (
                DepositPolicy.objects.select_for_update()
                .filter(
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
                or not isinstance(old_policy.get("course_kinds"), list)
            ):
                raise CommandError(
                    "A valid saved £4,000 ARU deposit policy is required."
                )

            new_policy = copy.deepcopy(old_policy)
            new_policy["course_ids"] = sorted(
                set(old_policy["course_ids"]) | set(IDS)
            )
            new_policy["course_kinds"] = sorted(
                set(old_policy["course_kinds"])
                | {"undergraduate_topup", "postgraduate_taught"}
            )

            report["scholarship_scope"] = {
                "changed": old_rule != new_rule,
                "before": old_rule,
                "after": new_rule,
            }
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
                    for name, value in after["course"].items():
                        if getattr(course, name) != value:
                            setattr(course, name, value)
                            fields.append(name)

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

                if old_rule != new_rule:
                    rule.rules = new_rule
                    rule.reviewed_at = timezone.now()
                    rule.save(update_fields=["rules", "reviewed_at"])

                if old_policy != new_policy:
                    policy.rules = new_policy
                    policy.save(update_fields=["rules", "updated_at"])

                report["database_changes"] = (
                    any(r["changed"] for r in report["records"])
                    or old_rule != new_rule
                    or old_policy != new_policy
                )

        save_report()

        for record in report["records"]:
            self.stdout.write(
                f"{record['course_id']}: GBP "
                f"{record['before']['tuition_fee']} -> "
                f"{record['after']['tuition_fee']} — "
                f"{'update' if record['changed'] else 'unchanged'}"
            )
            for field in ("title", "duration"):
                old = record["before"]["course"][field]
                new = record["after"]["course"][field]
                if old != new:
                    self.stdout.write(f"  {field}: {old} -> {new}")

        changes = (
            sum(r["changed"] for r in report["records"])
            + int(old_rule != new_rule)
            + int(old_policy != new_policy)
        )
        self.stdout.write(
            f"\nARU graduate scholarship scope: "
            f"{len(new_rule['conditions']['course_id'])} courses"
        )
        self.stdout.write(
            f"Deposit scope: {len(new_policy['course_ids'])} courses"
        )
        self.stdout.write(f"Proposed/updated items: {changes}")
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Reviewed route, fee and scope updates saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Top-up scholarship remains pending entry-year confirmation. "
            "Numeric deposits unchanged; PG awards remain conditional."
        )