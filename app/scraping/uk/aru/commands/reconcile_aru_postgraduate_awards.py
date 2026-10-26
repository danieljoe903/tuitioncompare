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
    University,
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    Scholarship,
    ScholarshipRule,
    DepositPolicy,
)
from app.scraping.uk.aru.commands.inspect_aru_sources import Command as SourceInspector
from app.scraping.uk.aru.commands.import_aru_postgraduate_fees import BATCH, YEAR
from app.scraping.uk.aru.commands.reconcile_aru_undergraduate_awards import DEPOSIT_URL, snapshot


SCHOLARSHIP_URL = (
    "https://www.aru.ac.uk/international/"
    "international-scholarships/"
    "international-postgraduate-scholarship"
)

IDS = sorted(f"C_UK003_{row[0]}" for row in BATCH)
MBA_IDS = ["C_UK003_80"]
OTHER_IDS = sorted(set(IDS) - set(MBA_IDS))

COMMON_QUESTIONS = [
    {
        "key": "aru_pg_entry_requirements_exceeded",
        "question": (
            "Has ARU confirmed that your qualifications exceed "
            "the minimum entry requirements for this scholarship?"
        ),
        "type": "boolean",
    },
    {
        "key": "registration_conditions_met",
        "question": "Have all ARU registration conditions been met?",
        "type": "boolean",
    },
    {
        "key": "aru_funding_eligibility_confirmed",
        "question": (
            "Is your funding arrangement eligible, including "
            "any sponsorship or federal tuition loan?"
        ),
        "type": "boolean",
    },
    {
        "key": "aru_pg_eligibility_confirmed",
        "question": (
            "Has ARU confirmed your eligibility for its "
            "International Postgraduate Scholarship?"
        ),
        "type": "boolean",
        "note": (
            "The published maximum is £4,000. "
            "Your actual award depends on ARU's assessment."
        ),
    },
]


def build_rule(course_ids, graduate_required):
    conditions = {
        "course_id": course_ids,
        "fee_status": ["INTL"],
        "aru_pg_entry_requirements_exceeded": [True],
        "registration_conditions_met": [True],
        "aru_funding_eligibility_confirmed": [True],
        "aru_pg_eligibility_confirmed": [True],
    }

    questions = copy.deepcopy(COMMON_QUESTIONS)

    if graduate_required:
        conditions["aru_graduate"] = [True]
        questions.insert(0, {
            "key": "aru_graduate",
            "question": "Are you an ARU graduate?",
            "type": "boolean",
        })

    return {
        "kind": "fixed_maximum",
        "value": "4000.00",
        "currency": "GBP",
        "benefit": "tuition",
        "academic_year": YEAR,
        "study_years": [1],
        "award_status": "conditional",
        "stacking": "exclusive",
        "compatible_ids": [],
        "percentage_basis": "original_tuition",
        "fee_basis_confirmed": True,
        "conditions": conditions,
        "eligibility_questions": questions,
        "scope_note": (
            "Reviewed September 2027 standard campus courses only. "
            "First compared study year. This models the published "
            "maximum, not a confirmed individual award."
        ),
        "combination_note": (
            "No other scholarship is combined automatically. "
            "Payment discounts require separate verification."
        ),
        "source_period_note": (
            "The current scholarship page does not specify an "
            "academic year. Associated with the reviewed 2027/28 "
            "cohort and subject to rechecking before enrolment."
        ),
    }


class Command(SourceInspector):
    help = (
        "Reconcile scholarship applicability and extend the "
        "saved ARU deposit policy to 15 reviewed PG courses."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        folder = root / "logs" / "aru" / f"pg-awards-{stamp}"
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
                "up to £4,000",
                "depending on qualifications",
                "be studying for an mba or be an aru graduate",
                "exceeding the minimum entry requirements",
                "all conditions of registration",
                "federal tuition fee loans",
            ]:
                if phrase not in scholarship_text:
                    raise ValueError(
                        f"Scholarship source needs review: {phrase}"
                    )

            if (
                "deposit for undergraduate and postgraduate "
                "taught courses is £4,000"
            ) not in deposit_text.casefold():
                raise ValueError(
                    "The published deposit amount needs review."
                )

        except (requests.RequestException, ValueError) as exc:
            report["error"] = str(exc)
            save_report()
            raise CommandError(
                f"Source verification stopped: {exc}. "
                f"No changes applied. Report: {output}"
            ) from exc

        with transaction.atomic():
            university = University.objects.select_for_update().get(
                university_id="UK003"
            )

            tuitions = {}
            sources = {}

            # Require the reviewed tuition import to be applied first.
            for row in BATCH:
                suffix, slug, title, heading, campus, fee = row
                course_id = f"C_UK003_{suffix}"

                try:
                    course = (
                        Course.objects.select_for_update()
                        .get(
                            course_id=course_id,
                            university=university,
                            title=title,
                            is_active=True,
                            start_month="September",
                            start_year=2027,
                            level__in=["PG", "PGT"],
                        )
                    )
                except Course.DoesNotExist as exc:
                    raise CommandError(
                        f"{course_id}: apply the reviewed PG "
                        "tuition import first."
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
                    != Decimal(fee)
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
                    != "postgraduate_taught"
                ):
                    raise CommandError(
                        f"{course_id}: verified PG tuition "
                        "source is required."
                    )

                tuitions[course_id] = tuition
                sources[course_id] = source

            policy = (
                DepositPolicy.objects.select_for_update()
                .filter(
                    university=university,
                    academic_year=YEAR,
                    currency="GBP",
                    verified=True,
                )
                .first()
            )

            if policy is None:
                raise CommandError(
                    "Apply reconcile_aru_undergraduate_awards first "
                    "to create the reviewed deposit policy."
                )

            old_policy = copy.deepcopy(policy.rules)

            if (
                not isinstance(old_policy, dict)
                or old_policy.get("kind") != "fixed"
                or old_policy.get("amount") != "4000.00"
                or old_policy.get("schema_version") != 1
                or old_policy.get("academic_year") != YEAR
                or not isinstance(
                    old_policy.get("course_ids"), list
                )
                or not isinstance(
                    old_policy.get("course_kinds"), list
                )
            ):
                raise CommandError(
                    "The saved deposit policy needs review "
                    "before its scope can be extended."
                )

            groups = [
                ("MBA eligibility", MBA_IDS, False),
                ("ARU graduate eligibility", OTHER_IDS, True),
            ]

            for label, scope, graduate_required in groups:
                name = (
                    "ARU International Postgraduate Scholarship "
                    f"— {label} ({YEAR})"
                )

                matches = list(
                    Scholarship.objects.select_for_update().filter(
                        university=university,
                        name=name,
                        level="PG",
                    )
                )

                if len(matches) > 1:
                    raise CommandError(
                        f"Duplicate scholarship records: {name}"
                    )

                award = matches[0] if matches else None
                saved_rule = (
                    ScholarshipRule.objects.select_for_update()
                    .filter(scholarship=award)
                    .first()
                    if award else None
                )

                fields = {
                    "award_type": "Tuition Reduction",
                    "award_value": "4000.00",
                    "currency": "GBP",
                    "deadline": None,
                    "eligibility": (
                        "Eligible MBA students."
                        if not graduate_required else
                        "Eligible ARU graduates on reviewed PG courses."
                    ),
                    "requirements": (
                        "Exceed minimum entry requirements; "
                        "registration conditions met; eligible "
                        "funding; ARU eligibility confirmation."
                    ),
                    "description": (
                        "Up to £4,000 depending on qualifications. "
                        "A maximum estimate, not a guaranteed "
                        "individual award."
                    ),
                    "url": SCHOLARSHIP_URL,
                }

                rule = {
                    "verified": True,
                    "source_url": SCHOLARSHIP_URL,
                    "rules": build_rule(
                        scope, graduate_required
                    ),
                }

                before = {
                    "scholarship": snapshot(award, fields),
                    "rule": snapshot(
                        saved_rule,
                        ["verified", "source_url", "rules"],
                    ),
                }
                after = {
                    "scholarship": fields,
                    "rule": rule,
                }
                changed = before != after

                report["proposals"].append({
                    "type": "scholarship",
                    "name": name,
                    "course_count": len(scope),
                    "changed": changed,
                    "before": before,
                    "after": after,
                })

                if options["apply"] and changed:
                    if award is None:
                        award = Scholarship.objects.create(
                            university=university,
                            name=name,
                            level="PG",
                            **fields,
                        )
                    else:
                        for key, value in fields.items():
                            setattr(award, key, value)
                        award.save(update_fields=list(fields))

                    ScholarshipRule.objects.update_or_create(
                        scholarship=award,
                        defaults={
                            **rule,
                            "reviewed_at": timezone.now(),
                        },
                    )

            new_policy = copy.deepcopy(old_policy)
            new_policy["course_ids"] = sorted(
                set(old_policy["course_ids"]) | set(IDS)
            )
            new_policy["course_kinds"] = sorted(
                set(old_policy["course_kinds"])
                | {"postgraduate_taught"}
            )

            policy_changed = old_policy != new_policy

            report["proposals"].append({
                "type": "deposit_scope",
                "changed": policy_changed,
                "before": old_policy,
                "after": new_policy,
            })

            if options["apply"] and policy_changed:
                policy.rules = new_policy
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

                new_raw = {
                    **copy.deepcopy(source.raw_fees),
                    "scholarship_status": (
                        "verified_maximum_rule_eligibility_required"
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
            if item["type"] == "scholarship":
                self.stdout.write(
                    f"{item['name']}: "
                    f"{item['course_count']} courses — "
                    f"{'update' if item['changed'] else 'unchanged'}"
                )

        self.stdout.write(
            "£4,000 deposit scope: "
            f"{len(new_policy['course_ids'])} total courses — "
            f"{'update' if policy_changed else 'unchanged'}"
        )
        self.stdout.write(
            "Proposed/updated items: "
            f"{sum(p['changed'] for p in report['proposals'])}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "PG scholarship rules, deposit scope and metadata saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Gross tuition and numeric stored deposits unchanged. "
            "£4,000 scholarship is a conditional maximum estimate."
        )