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
from app.management.commands.inspect_aru_sources import (
    Command as SourceInspector,
)
from app.management.commands.import_aru_embedded_postgraduate_fees import (
    BATCH,
    YEAR,
)
from app.management.commands.reconcile_aru_postgraduate_awards import (
    SCHOLARSHIP_URL,
)
from app.management.commands.reconcile_aru_undergraduate_awards import (
    DEPOSIT_URL,
)


IDS = sorted(f"C_UK003_{row[0]}" for row in BATCH)

ALUMNI_URL = (
    "https://www.aru.ac.uk/student-life/preparing-for-study/"
    "help-with-finances/scholarships/alumni-scholarship"
)

MUSIC_BURSARY_URL = (
    "https://www.aru.ac.uk/student-life/preparing-for-study/"
    "help-with-finances/scholarships/music-therapy-bursary"
)


class Command(SourceInspector):
    help = (
        "Extend the existing ARU graduate scholarship rule and "
        "deposit scope to ten embedded-data postgraduate courses."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        folder = root / "logs" / "aru" / f"embedded-pg-awards-{stamp}"
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
                    ("international_pg", SCHOLARSHIP_URL),
                    ("deposit", DEPOSIT_URL),
                    ("alumni", ALUMNI_URL),
                    ("music_bursary", MUSIC_BURSARY_URL),
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

            pg_text = (
                report["sources"]["international_pg"]["text"].casefold()
            )
            deposit_text = report["sources"]["deposit"]["text"]
            alumni_text = (
                report["sources"]["alumni"]["text"].casefold()
            )
            music_text = (
                report["sources"]["music_bursary"]["text"].casefold()
            )

            for phrase in [
                "up to £4,000",
                "depending on qualifications",
                "be studying for an mba or be an aru graduate",
                "exceeding the minimum entry requirements",
                "all conditions of registration",
            ]:
                if phrase not in pg_text:
                    raise ValueError(
                        f"International PG terms need review: {phrase}"
                    )

            if (
                "deposit for undergraduate and postgraduate "
                "taught courses is £4,000"
            ) not in deposit_text.casefold():
                raise ValueError(
                    "The published deposit amount needs review."
                )

            if not all(
                phrase in alumni_text
                for phrase in [
                    "20%",
                    "2026-27",
                    "international students",
                ]
            ):
                raise ValueError(
                    "The separate Alumni Scholarship terms changed; "
                    "review applicability before continuing."
                )

            if not all(
                phrase in music_text
                for phrase in [
                    "up to £1,000",
                    "entry bursary 2026",
                    "1 october",
                ]
            ):
                raise ValueError(
                    "Music Therapy bursary terms changed; "
                    "review the new award period before continuing."
                )

        except (requests.RequestException, ValueError) as exc:
            report["error"] = str(exc)
            save_report()
            raise CommandError(
                f"Source review stopped: {exc}. "
                f"No changes applied. Report: {output}"
            ) from exc

        with transaction.atomic():
            tuitions = {}
            sources = {}

            for (
                suffix, slug, old_title, new_title, heading,
                route_duration, old_duration, new_duration,
                campus, expected_fee
            ) in BATCH:
                course_id = f"C_UK003_{suffix}"

                try:
                    course = (
                        Course.objects.select_for_update()
                        .get(
                            course_id=course_id,
                            university_id="UK003",
                            title=new_title,
                            duration=new_duration,
                            location=f"{campus} Campus",
                            level__in=["PG", "PGT"],
                            start_month="September",
                            start_year=2027,
                            is_active=True,
                        )
                    )
                except Course.DoesNotExist as exc:
                    raise CommandError(
                        f"{course_id}: apply "
                        "import_aru_embedded_postgraduate_fees "
                        "first. The reviewed course does not match."
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
                        f"{course_id}: reviewed GBP tuition differs."
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
                    != "postgraduate_taught"
                ):
                    raise CommandError(
                        f"{course_id}: verified PG annual-fee "
                        "source is required."
                    )

                tuitions[course_id] = tuition
                sources[course_id] = source

            award_name = (
                "ARU International Postgraduate Scholarship "
                f"— ARU graduate eligibility ({YEAR})"
            )

            awards = list(
                Scholarship.objects.select_for_update().filter(
                    university_id="UK003",
                    name=award_name,
                    level="PG",
                )
            )

            if len(awards) != 1:
                raise CommandError(
                    "Exactly one existing ARU graduate "
                    "scholarship record is required."
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
                    "The verified ARU graduate rule is missing."
                )

            before_rule = copy.deepcopy(saved_rule.rules)

            if not isinstance(before_rule, dict):
                raise CommandError("Invalid saved scholarship rule.")

            conditions = before_rule.get("conditions")

            if (
                before_rule.get("kind") != "fixed_maximum"
                or before_rule.get("value") != "4000.00"
                or before_rule.get("academic_year") != YEAR
                or before_rule.get("study_years") != [1]
                or not isinstance(conditions, dict)
                or conditions.get("aru_graduate") != [True]
                or not isinstance(conditions.get("course_id"), list)
                or not before_rule.get("eligibility_questions")
            ):
                raise CommandError(
                    "Existing scholarship conditions need review."
                )

            after_rule = copy.deepcopy(before_rule)
            after_rule["conditions"]["course_id"] = sorted(
                set(conditions["course_id"]) | set(IDS)
            )
            rule_changed = before_rule != after_rule

            report["proposals"].append({
                "type": "scholarship_scope",
                "changed": rule_changed,
                "before": before_rule,
                "after": after_rule,
            })

            if options["apply"] and rule_changed:
                saved_rule.rules = after_rule
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
                | {"postgraduate_taught"}
            )
            policy_changed = before_policy != after_policy

            report["proposals"].append({
                "type": "deposit_scope",
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
                    "rules", "source_text", "source_sha256",
                    "checked_at", "updated_at",
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

                additional_reviews = [
                    {
                        "award": "Separate 20% Alumni Scholarship",
                        "source_url": ALUMNI_URL,
                        "status": "not_applied",
                        "reason": (
                            "The published page specifies 2026/27 "
                            "and UK/eligible EU students. International "
                            "students are directed to the International "
                            "Postgraduate Scholarship."
                        ),
                    }
                ]

                if course_id == "C_UK003_57":
                    additional_reviews.append({
                        "award": "Music Therapy Entry Bursary",
                        "source_url": MUSIC_BURSARY_URL,
                        "status": "pending_2027_confirmation",
                        "reason": (
                            "Up to £1,000, discretionary, and may "
                            "support fees or other costs. The page "
                            "still references a 2026 application. "
                            "No 2027 tuition deduction imported."
                        ),
                    })

                new_raw = {
                    **copy.deepcopy(source.raw_fees),
                    "scholarship_status": (
                        "verified_maximum_rule_eligibility_required"
                    ),
                    "deposit_status": (
                        "verified_current_policy_offer_confirmation_required"
                    ),
                    "unverified_legacy_discount_disabled": True,
                    "additional_award_reviews": additional_reviews,
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

        self.stdout.write(
            "International PG scholarship — ARU graduate scope: "
            f"{len(after_rule['conditions']['course_id'])} courses — "
            f"{'update' if rule_changed else 'unchanged'}"
        )
        self.stdout.write(
            "£4,000 deposit scope: "
            f"{len(after_policy['course_ids'])} courses — "
            f"{'update' if policy_changed else 'unchanged'}"
        )
        self.stdout.write(
            "Music Therapy bursary: pending 2027 confirmation; "
            "no deduction."
        )
        self.stdout.write(
            "Separate 20% Alumni Scholarship: not applied "
            "to these international 2027/28 comparisons."
        )
        self.stdout.write(
            "Proposed/updated items: "
            f"{sum(p['changed'] for p in report['proposals'])}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Scholarship scope, deposit scope and review metadata saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Gross tuition and numeric deposits unchanged. "
            "Scholarship remains a conditional maximum estimate."
        )