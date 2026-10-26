import copy
import json
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
    Scholarship,
    ScholarshipRule,
    DepositPolicy,
)
from app.scraping.uk.aru.commands.inspect_aru_sources import Command as SourceInspector
from app.scraping.uk.aru.commands.import_aru_corrected_undergraduate_fees import BATCH, YEAR
from app.scraping.uk.aru.commands.reconcile_aru_undergraduate_awards import SCHOLARSHIP_URL, DEPOSIT_URL


IDS = sorted(f"C_UK003_{row[0]}" for row in BATCH)


class Command(SourceInspector):
    help = (
        "Extend existing ARU undergraduate scholarship rules "
        "and deposit scope to eight corrected courses."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        folder = root / "logs" / "aru" / f"corrected-ug-awards-{stamp}"
        folder.mkdir(parents=True, exist_ok=False)

        output = folder / "reconciliation.json"
        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
            "university_id": "UK003",
            "academic_year": YEAR,
            "course_ids": IDS,
            "sources": {},
            "proposals": [],
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

                for name, url in [
                    ("scholarship", SCHOLARSHIP_URL),
                    ("deposit", DEPOSIT_URL),
                ]:
                    response, soup = self.get_page(session, url)

                    if response.url.rstrip("/") != url.rstrip("/"):
                        raise ValueError(
                            f"Unexpected redirect: {response.url}"
                        )

                    report["sources"][name] = self.evidence(
                        response, soup
                    )
                    (folder / f"{name}.html").write_bytes(
                        response.content
                    )

            scholarship_text = (
                report["sources"]["scholarship"]["text"].casefold()
            )
            deposit_text = report["sources"]["deposit"]["text"]

            for phrase in [
                "£2,000 in the first year",
                "£4,000 in the first year",
                "all conditions of registration",
                "cannot be combined with any other scholarships",
            ]:
                if phrase not in scholarship_text:
                    raise ValueError(
                        f"Scholarship terms need review: {phrase}"
                    )

            if (
                "deposit for undergraduate and postgraduate "
                "taught courses is £4,000"
            ) not in deposit_text.casefold():
                raise ValueError(
                    "The published £4,000 deposit needs review."
                )

        except (requests.RequestException, ValueError) as exc:
            report["error"] = str(exc)
            save_report()
            raise CommandError(
                f"Source verification stopped: {exc}. "
                f"No changes applied. Report: {output}"
            ) from exc

        with transaction.atomic():
            tuitions = {}
            sources = {}

            for (
                suffix, slug, subject, old_award, new_award,
                old_campus, new_campus, ucas, expected_fee
            ) in BATCH:
                course_id = f"C_UK003_{suffix}"

                try:
                    course = (
                        Course.objects.select_for_update()
                        .get(
                            course_id=course_id,
                            university_id="UK003",
                            title=f"{new_award} (Hons) {subject}",
                            location=f"{new_campus} Campus",
                            level="UG",
                            duration="3 Years",
                            start_month="September",
                            start_year=2027,
                            ucas_cao_code=ucas,
                            is_active=True,
                        )
                    )
                except Course.DoesNotExist as exc:
                    raise CommandError(
                        f"{course_id}: apply "
                        "import_aru_corrected_undergraduate_fees "
                        "first. The corrected course does not match."
                    ) from exc

                rows = list(
                    Tuition.objects.select_for_update().filter(
                        course=course,
                        residency="INTL",
                    )
                )

                if (
                    len(rows) != 1
                    or rows[0].currency != "GBP"
                    or Decimal(str(rows[0].tuition_fee))
                    != Decimal(expected_fee)
                ):
                    raise CommandError(
                        f"{course_id}: reviewed GBP tuition "
                        "does not match."
                    )

                tuition = rows[0]
                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                if (
                    source is None
                    or source.academic_year != YEAR
                    or source.fee_basis != "annual_tuition"
                    or not isinstance(source.raw_fees, dict)
                    or source.raw_fees.get("verified") is not True
                    or source.raw_fees.get("course_kind")
                    != "undergraduate_taught"
                ):
                    raise CommandError(
                        f"{course_id}: verified undergraduate "
                        "tuition source is required."
                    )

                tuitions[course_id] = tuition
                sources[course_id] = source

            for tier, amount in [
                ("Lower", "2000.00"),
                ("Higher", "4000.00"),
            ]:
                name = (
                    "ARU International Undergraduate Scholarship "
                    f"— {tier} — Year 1 ({YEAR})"
                )

                awards = list(
                    Scholarship.objects.select_for_update().filter(
                        university_id="UK003",
                        name=name,
                        level="UG",
                    )
                )

                if len(awards) != 1:
                    raise CommandError(
                        f"Exactly one existing scholarship "
                        f"is required: {name}"
                    )

                saved_rule = (
                    ScholarshipRule.objects.select_for_update()
                    .filter(
                        scholarship=awards[0],
                        verified=True,
                        source_url=SCHOLARSHIP_URL,
                    )
                    .first()
                )

                if saved_rule is None:
                    raise CommandError(
                        f"Verified saved rule is missing: {name}"
                    )

                before = copy.deepcopy(saved_rule.rules)

                if not isinstance(before, dict):
                    raise CommandError(
                        f"Invalid saved rule: {name}"
                    )

                conditions = before.get("conditions")

                if (
                    before.get("kind") != "fixed"
                    or before.get("value") != amount
                    or before.get("currency") != "GBP"
                    or before.get("academic_year") != YEAR
                    or before.get("study_years") != [1]
                    or not isinstance(conditions, dict)
                    or not isinstance(
                        conditions.get("course_id"), list
                    )
                    or conditions.get("aru_ug_award_tier")
                    != [tier.lower()]
                    or not before.get("eligibility_questions")
                ):
                    raise CommandError(
                        f"Saved scholarship conditions need review: "
                        f"{name}"
                    )

                after = copy.deepcopy(before)
                after["conditions"]["course_id"] = sorted(
                    set(conditions["course_id"]) | set(IDS)
                )
                changed = before != after

                report["proposals"].append({
                    "type": "scholarship_scope",
                    "name": name,
                    "course_count": len(
                        after["conditions"]["course_id"]
                    ),
                    "changed": changed,
                    "before": before,
                    "after": after,
                })

                if options["apply"] and changed:
                    saved_rule.rules = after
                    saved_rule.reviewed_at = timezone.now()
                    saved_rule.save(update_fields=[
                        "rules", "reviewed_at"
                    ])

            policy = (
                DepositPolicy.objects.select_for_update()
                .filter(
                    university_id="UK003",
                    academic_year=YEAR,
                    currency="GBP",
                    verified=True,
                )
                .first()
            )

            if policy is None:
                raise CommandError(
                    "The verified ARU deposit policy is missing."
                )

            before_policy = copy.deepcopy(policy.rules)

            if (
                not isinstance(before_policy, dict)
                or before_policy.get("schema_version") != 1
                or before_policy.get("kind") != "fixed"
                or before_policy.get("amount") != "4000.00"
                or before_policy.get("academic_year") != YEAR
                or not isinstance(
                    before_policy.get("course_ids"), list
                )
                or not isinstance(
                    before_policy.get("course_kinds"), list
                )
            ):
                raise CommandError(
                    "The saved deposit policy needs review."
                )

            after_policy = copy.deepcopy(before_policy)
            after_policy["course_ids"] = sorted(
                set(before_policy["course_ids"]) | set(IDS)
            )
            after_policy["course_kinds"] = sorted(
                set(before_policy["course_kinds"])
                | {"undergraduate_taught"}
            )

            policy_changed = before_policy != after_policy

            report["proposals"].append({
                "type": "deposit_scope",
                "course_count": len(after_policy["course_ids"]),
                "changed": policy_changed,
                "before": before_policy,
                "after": after_policy,
            })

            if options["apply"] and policy_changed:
                policy.rules = after_policy
                policy.source_text = deposit_text
                policy.source_sha256 = (
                    report["sources"]["deposit"]["sha256"]
                )
                policy.checked_at = timezone.now()
                policy.save(update_fields=[
                    "rules",
                    "source_text",
                    "source_sha256",
                    "checked_at",
                    "updated_at",
                ])

            for course_id in IDS:
                tuition = tuitions[course_id]
                source = sources[course_id]

                before = {
                    "scholarship": str(tuition.scholarship),
                    "scholarship_source_id": (
                        tuition.scholarship_source_id
                    ),
                    "raw_fees": copy.deepcopy(source.raw_fees),
                }

                # Preserve the fee evidence and correction history.
                new_raw = {
                    **copy.deepcopy(source.raw_fees),
                    "scholarship_status": (
                        "verified_rule_eligibility_required"
                    ),
                    "deposit_status": (
                        "verified_current_policy_offer_confirmation_required"
                    ),
                    "unverified_legacy_discount_disabled": True,
                }

                after = {
                    "scholarship": "0.00",
                    "scholarship_source_id": None,
                    "raw_fees": new_raw,
                }

                changed = (
                    tuition.scholarship != 0
                    or tuition.scholarship_source_id is not None
                    or source.raw_fees != new_raw
                )

                report["proposals"].append({
                    "type": "course_metadata_and_legacy_discount",
                    "course_id": course_id,
                    "changed": changed,
                    "before": before,
                    "after": after,
                })

                if options["apply"] and changed:
                    tuition.scholarship = 0
                    tuition.scholarship_source = None
                    tuition.save(update_fields=[
                        "scholarship", "scholarship_source"
                    ])

                    source.raw_fees = new_raw
                    source.save(update_fields=[
                        "raw_fees", "checked_at"
                    ])

                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=course_id,
                        residency="INTL",
                        before=before,
                        after=after,
                    )

            report["database_changes"] = (
                options["apply"]
                and any(
                    item["changed"]
                    for item in report["proposals"]
                )
            )

        save_report()

        for item in report["proposals"]:
            if item["type"] == "scholarship_scope":
                self.stdout.write(
                    f"{item['name']}: "
                    f"{item['course_count']} courses — "
                    f"{'update' if item['changed'] else 'unchanged'}"
                )

        self.stdout.write(
            "£4,000 deposit scope: "
            f"{len(after_policy['course_ids'])} courses — "
            f"{'update' if policy_changed else 'unchanged'}"
        )
        self.stdout.write(
            "Proposed/updated items: "
            f"{sum(p['changed'] for p in report['proposals'])}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Scholarship and deposit scopes extended."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Gross tuition, correction history and numeric deposits "
            "preserved. Unverified legacy discounts are cleared "
            "on apply; scholarship deductions remain conditional."
        )