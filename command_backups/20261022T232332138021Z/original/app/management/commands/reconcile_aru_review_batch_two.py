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
from app.management.commands.inspect_aru_sources import (
    Command as SourceInspector,
)
from app.management.commands.import_aru_corrected_undergraduate_fees import (
    standard_fee_evidence,
)
from app.management.commands.reconcile_aru_nursing_mba_restriction import (
    business_snapshot,
)


YEAR = "2027/28"
BASE = "https://www.aru.ac.uk/study/undergraduate/"

BATCH = [
    {
        "id": "C_UK003_45",
        "slug": "midwifery",
        "heading": "Midwifery",
        "old_title": "BSc (Hons) Midwifery",
        "title": "BSc (Hons) Midwifery",
        "award": "BSc (Hons)",
        "duration": "3 Years",
        "campus": "Chelmsford",
        "ucas": "B720",
        "fee": "22700.00",
    },
    {
        "id": "C_UK003_92",
        "slug": "animation-and-illustration",
        "heading": "Animation and Illustration",
        "old_title": "BA (Hons) Illustration and Animation",
        "title": "BA (Hons) Animation and Illustration",
        "award": "BA (Hons)",
        "duration": "3 Years",
        "campus": "Cambridge",
        "ucas": "WW26",
        "fee": "19750.00",
    },
    {
        "id": "C_UK003_44",
        "slug": "medicine",
        "heading": "Medicine",
        "old_title": "MBChB Medicine",
        "title": "MBChB Medicine",
        "award": "MBChB",
        "duration": "5 Years",
        "campus": "Chelmsford",
        "ucas": "A100",
        "fee": None,
    },
]

ELIGIBLE_IDS = ["C_UK003_45", "C_UK003_92"]
RESTRICTED_ID = "C_UK003_44"

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


class Command(SourceInspector):
    help = (
        "Reconcile Midwifery and Animation international fees, "
        "Medicine's home-fee restriction and applicable rule scopes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        folder = (
            Path(settings.BASE_DIR)
            / "logs" / "aru" / f"review-batch-two-{stamp}"
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
                    url = BASE + row["slug"]
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
                f"Source collection stopped: {exc}. "
                f"No changes applied. Report: {output}"
            ) from exc

        with transaction.atomic():
            pending = []

            for row in BATCH:
                course_id = row["id"]

                try:
                    page = report["sources"][course_id]
                    if page.get("title", "").strip() != row["heading"]:
                        raise ValueError(
                            "Published course heading differs."
                        )

                    body = page["text"].split(
                        "End of main navigation."
                    )[-1]

                    if not body.strip().startswith(
                        f"Undergraduate {row['award']}"
                    ):
                        raise ValueError(
                            "Published undergraduate award differs."
                        )

                    route = re.search(
                        r"Start date\s+(.*?)\s+Location\s+(.*?)"
                        r"\s+UCAS course code\s+"
                        + re.escape(row["ucas"])
                        + r"\s+Duration\s+"
                        + row["duration"].split()[0]
                        + r"\s+years\b",
                        body,
                    )

                    if (
                        route is None
                        or "September 2027" not in route.group(1)
                        or row["campus"] not in route.group(2).split()
                    ):
                        raise ValueError(
                            "Standard September 2027 campus "
                            "and UCAS route not confirmed."
                        )

                    restricted = row["fee"] is None

                    if restricted:
                        restriction = (
                            "must have home fee status at the "
                            "point of application"
                        )
                        if restriction not in body.casefold():
                            raise ValueError(
                                "Medicine's home-fee restriction "
                                "needs review."
                            )

                        amount = None
                        note = (
                            "ARU requires home fee status at the "
                            "point of application for MBChB Medicine. "
                            "This course is unavailable for an "
                            "international-fee comparison. "
                            "Fee status is assessed by ARU and "
                            "must not be inferred from nationality "
                            "or residence alone."
                        )
                    else:
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

                        amount = Decimal(row["fee"])
                        if amounts != {amount}:
                            raise ValueError(
                                "Published standard-route annual "
                                f"fee differs: {sorted(amounts)}."
                            )

                        note = (
                            "Published annual international tuition "
                            "for the standard three-year September "
                            f"2027 route at {row['campus']}. "
                            "Foundation and placement routes are "
                            "excluded. Living-cost grants and "
                            "bursaries are not automatically "
                            "deducted from tuition."
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
                        or course.level != "UG"
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

                    raw = {
                        "verified": not restricted,
                        "academic_year": "" if restricted else YEAR,
                        "advertised_intake": "September 2027",
                        "source_fee_category": "International",
                        "course_kind": "undergraduate_taught",
                        "currency": "GBP",
                        "study_year": 1,
                        "study_mode": "full_time",
                        "campus": row["campus"],
                        "course_source_url": BASE + row["slug"],
                        "source_sha256": page["sha256"],
                        "source_checked_at": page.get("checked_at"),
                        "fee_note": note,
                        "unverified_legacy_discount_disabled": True,
                        "scholarship_status": (
                            "not_applicable_to_international_fee_status"
                            if restricted
                            else "verified_rule_eligibility_required"
                        ),
                        "deposit_status": (
                            "not_applicable_to_international_fee_status"
                            if restricted
                            else (
                                "verified_current_policy_"
                                "offer_confirmation_required"
                            )
                        ),
                    }

                    if restricted:
                        raw.update({
                            "required_fee_status": "HOME",
                            "verification_reason": note,
                            "fee_categories": [],
                        })
                    else:
                        raw.update({
                            "annual_tuition": str(amount),
                            "fee_method": "reviewed_standard_route_fee",
                            "standard_route_fee_evidence": excerpt,
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
                        "course": {
                            **before["course"],
                            "title": row["title"],
                            "start_month": "September",
                            "start_year": 2027,
                            "course_url": BASE + row["slug"],
                            "ucas_cao_code": row["ucas"],
                        },
                        "tuition_fee": (
                            before["tuition_fee"]
                            if restricted else str(amount)
                        ),
                        "scholarship": "0.00",
                        "scholarship_source_id": None,
                        "source": {
                            "academic_year": "" if restricted else YEAR,
                            "source_url": BASE + row["slug"],
                            "source_course_code": row["ucas"],
                            "source_course_name": row["title"],
                            "fee_basis": (
                                "unavailable"
                                if restricted else "annual_tuition"
                            ),
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

            pending_rules = []

            for tier, value in [
                ("Lower", "2000.00"),
                ("Higher", "4000.00"),
            ]:
                name = (
                    "ARU International Undergraduate Scholarship "
                    f"— {tier} — Year 1 ({YEAR})"
                )
                matches = list(
                    ScholarshipRule.objects.select_for_update().filter(
                        scholarship__university_id="UK003",
                        scholarship__name=name,
                        scholarship__level="UG",
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
                    or old.get("kind") != "fixed"
                    or old.get("value") != value
                    or old.get("study_years") != [1]
                    or conditions.get("aru_ug_award_tier")
                    != [tier.lower()]
                    or not isinstance(conditions.get("course_id"), list)
                    or not old.get("eligibility_questions")
                ):
                    raise CommandError(
                        f"Saved scholarship rule needs review: {name}"
                    )

                new = copy.deepcopy(old)
                new["conditions"]["course_id"] = sorted(
                    (set(conditions["course_id"]) | set(ELIGIBLE_IDS))
                    - {RESTRICTED_ID}
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
                    "A valid saved £4,000 ARU deposit policy is required."
                )

            new_policy = copy.deepcopy(old_policy)
            new_policy["course_ids"] = sorted(
                (set(old_policy["course_ids"]) | set(ELIGIBLE_IDS))
                - {RESTRICTED_ID}
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
            description = (
                "Home fee status required; numeric tuition preserved"
                if record["restricted"]
                else (
                    f"GBP {record['before']['tuition_fee']} -> "
                    f"{record['after']['tuition_fee']}"
                )
            )
            self.stdout.write(
                f"{record['course_id']}: {description} — "
                f"{'update' if record['changed'] else 'unchanged'}"
            )

            old_title = record["before"]["course"]["title"]
            new_title = record["after"]["course"]["title"]
            if old_title != new_title:
                self.stdout.write(
                    f"  title: {old_title} -> {new_title}"
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