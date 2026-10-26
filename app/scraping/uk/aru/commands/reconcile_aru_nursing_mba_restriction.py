import copy
import json
import re
from decimal import Decimal
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import CommandError
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
from app.scraping.uk.aru.commands.inspect_aru_sources import Command as SourceInspector


YEAR = "2027/28"
BASE = "https://www.aru.ac.uk/study/"

BATCH = [
    {
        "id": "C_UK003_13",
        "path": "undergraduate/nursing-adult",
        "old_title": "BSc (Hons) Adult Nursing",
        "title": "BSc (Hons) Nursing (Adult)",
        "level": "UG",
        "duration": "3 Years",
        "campus": "Chelmsford",
        "award": "BSc (Hons)",
        "ucas": "B740",
        "amount": "21975.00",
        "basis": "annual_tuition",
    },
    {
        "id": "C_UK003_25",
        "path": "postgraduate/master-of-business-administration-mba",
        "old_title": "MBA Master of Business Administration",
        "title": "MBA Master of Business Administration",
        "level": "PG",
        "duration": "1 Year",
        "campus": "Cambridge",
        "award": "MBA",
        "ucas": "",
        "amount": "22700.00",
        "basis": "full_time_course_fee",
    },
    {
        "id": "C_UK003_105",
        "path": "postgraduate/fintech-and-data-analytics",
        "old_title": "MBA FinTech and Data Analytics",
        "title": "MBA FinTech and Data Analytics",
        "level": "PG",
        "duration": "1 Year",
        "campus": "Cambridge",
        "award": "MBA",
        "ucas": "",
        "amount": "22700.00",
        "basis": "full_time_course_fee",
    },
    {
        "id": "C_ARU003_RES_03",
        "path": "postgraduate/life-sciences",
        "old_title": "MSc by Research (Life Sciences)",
        "title": "MRes Life Sciences",
        "level": "MRES",
        "duration": "1 Year",
        "campus": "Cambridge",
        "award": "MRes",
        "ucas": "",
        "amount": None,
        "basis": "unavailable",
    },
]

ELIGIBLE_IDS = [
    "C_UK003_13",
    "C_UK003_25",
    "C_UK003_105",
]
RESTRICTED_ID = "C_ARU003_RES_03"

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


def business_snapshot(value):
    """Ignore refreshed source hashes when checking substantive changes."""
    value = copy.deepcopy(value)
    source = value.get("source")
    if isinstance(source, dict):
        raw = source.get("raw_fees")
        if isinstance(raw, dict):
            raw.pop("source_sha256", None)
            raw.pop("source_checked_at", None)
    return value


class Command(SourceInspector):
    help = (
        "Reconcile ARU Nursing, two MBA fees and the "
        "Life Sciences international-applicant restriction."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        folder = (
            Path(settings.BASE_DIR)
            / "logs" / "aru" / f"nursing-mba-review-{stamp}"
        )
        folder.mkdir(parents=True, exist_ok=False)

        output = folder / "reconciliation.json"
        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
            "university_id": "UK003",
            "sources": {},
            "records": [],
            "scope_changes": [],
            "blocked": [],
        }

        def save_report():
            output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        try:
            with requests.Session() as session:
                session.headers["User-Agent"] = (
                    "TuitionCompareSourceInspector/1.0"
                )
                session.mount(
                    "https://", HTTPAdapter(max_retries=retry)
                )

                for row in BATCH:
                    url = BASE + row["path"]
                    response, soup = self.get_page(session, url)

                    if response.url.rstrip("/") != url.rstrip("/"):
                        raise ValueError(
                            f"Unexpected redirect: {response.url}"
                        )

                    report["sources"][row["id"]] = self.evidence(
                        response, soup
                    )
                    (folder / f"{row['id']}.html").write_bytes(
                        response.content
                    )

        except (requests.RequestException, ValueError) as exc:
            report["error"] = str(exc)
            save_report()
            raise CommandError(
                f"Collection stopped: {exc}. "
                f"No changes applied. Report: {output}"
            ) from exc

        with transaction.atomic():
            pending = []

            for row in BATCH:
                course_id = row["id"]

                try:
                    page = report["sources"][course_id]
                    body = page["text"].split(
                        "End of main navigation."
                    )[-1]
                    url = BASE + row["path"]

                    if row["basis"] == "unavailable":
                        if (
                            "programme is open to uk students only"
                            not in body.casefold()
                            or "Life Sciences MRes" not in body
                            or "September 2027" not in body
                        ):
                            raise ValueError(
                                "Life Sciences award, intake or "
                                "UK-only restriction needs review."
                            )

                        amount = None
                        note = (
                            "The published MRes Life Sciences page "
                            "states that the programme is open to "
                            "UK students only. International tuition "
                            "is unavailable; no home fee has been "
                            "assigned as an international fee."
                        )

                    else:
                        if row["level"] == "UG":
                            route = re.search(
                                r"Start date\s+(.*?)\s+Location\s+(.*?)"
                                r"\s+UCAS course code\s+B740"
                                r"\s+Duration\s+3 years\b",
                                body,
                            )
                            if not body.strip().startswith(
                                "Undergraduate BSc (Hons)"
                            ):
                                raise ValueError(
                                    "Published Nursing award differs."
                                )
                        else:
                            route = re.search(
                                r"Start date\s+(.*?)\s+Location\s+(.*?)"
                                r"\s+Award\s+MBA"
                                r"\s+Duration\s+1 year full-time\b",
                                body,
                            )
                            if not body.strip().startswith(
                                "Postgraduate MBA"
                            ):
                                raise ValueError(
                                    "Published MBA award differs."
                                )

                        if (
                            route is None
                            or "September 2027" not in route.group(1)
                            or row["campus"]
                            not in route.group(2).split()
                        ):
                            raise ValueError(
                                "Standard September 2027 campus "
                                "route is not confirmed."
                            )

                        marker = body.rfind("Fees and funding")
                        if marker < 0:
                            raise ValueError("Fee section is missing.")

                        fee_section = body[marker:]
                        label = (
                            "full-time, per year"
                            if row["basis"] == "annual_tuition"
                            else "full-time"
                        )

                        amounts = {
                            Decimal(value.replace(",", ""))
                            for value in re.findall(
                                r"£\s*([\d,]+(?:\.\d{2})?)"
                                r"\s+International students starting"
                                r"\s+2027/28\s+\("
                                + re.escape(label) + r"\)",
                                fee_section,
                            )
                        }

                        amount = Decimal(row["amount"])
                        if amounts != {amount}:
                            raise ValueError(
                                "Published fee differs or is ambiguous: "
                                f"{sorted(amounts)}."
                            )

                        note = (
                            "Published annual international tuition "
                            "for the standard September 2027 "
                            "three-year Nursing (Adult) route. "
                            "Living-cost training grants are not "
                            "deducted from tuition."
                            if row["level"] == "UG"
                            else
                            "Published international fee for the "
                            "standard September 2027 one-year "
                            "full-time MBA route. The source labels "
                            "this 'full-time', not 'per year'. "
                            "Optional Professional Experience "
                            "routes are excluded."
                        )

                    course = (
                        Course.objects.select_for_update()
                        .get(
                            course_id=course_id,
                            university_id="UK003",
                        )
                    )

                    if (
                        course.title not in {
                            row["old_title"], row["title"]
                        }
                        or course.level != row["level"]
                        or course.duration != row["duration"]
                        or course.location
                        != f"{row['campus']} Campus"
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
                            "tuition record is required."
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

                    restricted = amount is None
                    after_course = {
                        **before["course"],
                        "title": row["title"],
                        "start_month": "September",
                        "start_year": 2027,
                        "course_url": url,
                    }
                    if row["ucas"]:
                        after_course["ucas_cao_code"] = row["ucas"]

                    raw = {
                        "verified": not restricted,
                        "academic_year": "" if restricted else YEAR,
                        "advertised_intake": "September 2027",
                        "source_fee_category": "International",
                        "course_kind": (
                            "postgraduate_research"
                            if restricted
                            else (
                                "undergraduate_taught"
                                if row["level"] == "UG"
                                else "postgraduate_taught"
                            )
                        ),
                        "currency": "GBP",
                        "study_year": 1,
                        "study_mode": "full_time",
                        "course_source_url": url,
                        "source_sha256": page["sha256"],
                        "source_checked_at": page.get("checked_at"),
                        "fee_note": note,
                        "unverified_legacy_discount_disabled": True,
                        "scholarship_status": (
                            "not_applicable_to_international_applicants"
                            if restricted
                            else "verified_rule_eligibility_required"
                        ),
                        "deposit_status": (
                            "not_applicable_to_international_applicants"
                            if restricted
                            else (
                                "verified_current_policy_"
                                "offer_confirmation_required"
                            )
                        ),
                    }

                    if restricted:
                        raw.update({
                            "international_applicants_eligible": False,
                            "verification_reason": note,
                            "fee_categories": [],
                        })
                    else:
                        raw.update({
                            # Compatibility key for the existing
                            # calculator; fee_basis controls the label.
                            "annual_tuition": str(amount),
                            "published_tuition": str(amount),
                            "published_fee_description": (
                                "International students starting "
                                f"{YEAR} ({label})"
                            ),
                            "fee_categories": [{
                                "source_fee_category": "International",
                                "academic_year": YEAR,
                                "currency": "GBP",
                                "annual_tuition": str(amount),
                                "amount_requires_review": False,
                            }],
                        })

                    if row["old_title"] != row["title"]:
                        raw["reviewed_course_corrections"] = {
                            "title": {
                                "before": row["old_title"],
                                "after": row["title"],
                            }
                        }

                    after = {
                        "course": after_course,
                        "tuition_fee": (
                            before["tuition_fee"]
                            if restricted else str(amount)
                        ),
                        "scholarship": "0.00",
                        "scholarship_source_id": None,
                        "source": {
                            "academic_year": (
                                "" if restricted else YEAR
                            ),
                            "source_url": url,
                            "source_course_code": row["ucas"],
                            "source_course_name": row["title"],
                            "fee_basis": row["basis"],
                            "raw_fees": raw,
                        },
                    }

                    record = {
                        "course_id": course_id,
                        "restricted": restricted,
                        "changed": (
                            business_snapshot(before)
                            != business_snapshot(after)
                        ),
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
                    f"No changes applied. Review: {output}"
                )

            specs = [
                (
                    "ARU International Undergraduate Scholarship "
                    f"— Lower — Year 1 ({YEAR})",
                    "fixed", "2000.00", ["C_UK003_13"],
                ),
                (
                    "ARU International Undergraduate Scholarship "
                    f"— Higher — Year 1 ({YEAR})",
                    "fixed", "4000.00", ["C_UK003_13"],
                ),
                (
                    "ARU International Postgraduate Scholarship "
                    f"— MBA eligibility ({YEAR})",
                    "fixed_maximum", "4000.00",
                    ["C_UK003_25", "C_UK003_105"],
                ),
            ]

            pending_rules = []

            for name, kind, value, additions in specs:
                matches = list(
                    ScholarshipRule.objects.select_for_update().filter(
                        scholarship__university_id="UK003",
                        scholarship__name=name,
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
                    "A valid saved £4,000 deposit policy is required."
                )

            if RESTRICTED_ID in old_policy["course_ids"]:
                raise CommandError(
                    "Life Sciences is already in the deposit scope. "
                    "Review this conflicting policy before continuing."
                )

            new_policy = copy.deepcopy(old_policy)
            new_policy["course_ids"] = sorted(
                set(old_policy["course_ids"]) | set(ELIGIBLE_IDS)
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
            if record["restricted"]:
                description = (
                    "UK-only restriction; numeric tuition preserved"
                )
            else:
                description = (
                    f"GBP {record['before']['tuition_fee']} -> "
                    f"{record['after']['tuition_fee']}; "
                    f"{record['after']['source']['fee_basis']}"
                )

            self.stdout.write(
                f"{record['course_id']}: {description} — "
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
            "Reviewed fees, restriction and rule scopes saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )