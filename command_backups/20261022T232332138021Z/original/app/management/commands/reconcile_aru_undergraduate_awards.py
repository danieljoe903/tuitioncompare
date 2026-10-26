import copy
import hashlib
import json
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
from app.management.commands.inspect_aru_sources import (
    Command as SourceInspector,
)
from app.management.commands.import_aru_undergraduate_fees import (
    REVIEWED,
    ACADEMIC_YEAR,
)


SCHOLARSHIP_URL = (
    "https://www.aru.ac.uk/international/"
    "international-scholarships/"
    "international-undergraduate-scholarship"
)

DEPOSIT_URL = (
    "https://www.aru.ac.uk/international/how-to-apply/"
    "step-4---receive-our-decision-on-your-application"
)

IDS = sorted(f"C_UK003_{row[0]}" for row in REVIEWED)
WRITTLE_IDS = sorted(
    f"C_UK003_{row[0]}"
    for row in REVIEWED
    if row[4] == "Writtle"
)

QUESTIONS = [
    {
        "key": "new_entrant",
        "question": "Are you joining ARU for the first time?",
        "type": "boolean",
    },
    {
        "key": "full_time",
        "question": "Will you study full-time?",
        "type": "boolean",
        "note": "Exceptional part-time cases require ARU confirmation.",
    },
    {
        "key": "entry_year",
        "question": "Which course year are you entering?",
        "type": "integer",
        "note": "These rules cover entry into Year 1 only.",
    },
    {
        "key": "apcl",
        "question": (
            "Have you received Accredited Prior Certified "
            "Learning credit?"
        ),
        "type": "boolean",
        "note": "APCL discounts need a separate pro-rata assessment.",
    },
    {
        "key": "registration_conditions_met",
        "question": "Have all ARU registration conditions been met?",
        "type": "boolean",
    },
    {
        "key": "aru_funding_eligibility_confirmed",
        "question": (
            "Is your funding arrangement eligible for this award, "
            "including any sponsorship or federal tuition loan?"
        ),
        "type": "boolean",
        "note": (
            "Sponsored students and federal loan recipients may "
            "need confirmation from ARU and their funding body."
        ),
    },
    {
        "key": "aru_ug_award_confirmed",
        "question": "Has ARU confirmed your undergraduate scholarship?",
        "type": "boolean",
    },
    {
        "key": "aru_ug_award_tier",
        "question": "Which first-year award has ARU confirmed?",
        "type": "choice",
        "choices": [
            {"value": "lower", "label": "£2,000"},
            {"value": "higher", "label": "£4,000"},
        ],
        "note": "Exceeding entry requirements does not confirm a tier.",
    },
]


def snapshot(obj, fields):
    if obj is None:
        return None
    return {
        name: copy.deepcopy(getattr(obj, name))
        for name in fields
    }


def rule_data(course_ids, amount, tier):
    return {
        "kind": "fixed",
        "value": amount,
        "benefit": "tuition",
        "currency": "GBP",
        "academic_year": ACADEMIC_YEAR,
        "study_years": [1],
        "award_status": "conditional",
        "stacking": "exclusive",
        "compatible_ids": [],
        "percentage_basis": "original_tuition",
        "fee_basis_confirmed": True,
        "conditions": {
            "course_id": course_ids,
            "fee_status": ["INTL"],
            "new_entrant": [True],
            "full_time": [True],
            "entry_year": [1],
            "apcl": [False],
            "registration_conditions_met": [True],
            "aru_funding_eligibility_confirmed": [True],
            "aru_ug_award_confirmed": [True],
            "aru_ug_award_tier": [tier],
        },
        "scope_note": (
            "Reviewed standard campus undergraduate routes only. "
            "First compared study year and Year 1 entrants only. "
            "Unknown eligibility answers do not qualify."
        ),
        "combination_note": (
            "Do not combine with another scholarship. "
            "A Full Payment Discount may be permitted in Year 1, "
            "but is not modelled until its deadline and payment "
            "conditions have been separately verified."
        ),
        "future_year_note": (
            "ARU publishes £1,000 in subsequent years, for up to "
            "three subsequent years, subject to successful completion. "
            "Those future-year rules are not imported by this command."
        ),
        "source_period_note": (
            "Current published policy does not name an academic year. "
            "Saved against the reviewed 2027/28 course cohort; "
            "recheck before enrolment."
        ),
        "eligibility_questions": QUESTIONS,
    }


class Command(SourceInspector):
    help = (
        "Reconcile ARU first-year undergraduate scholarship rules "
        "and the current published deposit policy for 24 courses."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        folder = root / "logs" / "aru" / f"awards-{stamp}"
        folder.mkdir(parents=True, exist_ok=False)

        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
            "university_id": "UK003",
            "academic_year": ACADEMIC_YEAR,
            "course_ids": IDS,
            "proposals": [],
            "sources": {},
        }

        output = folder / "reconciliation.json"

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

            scholarship_text = report["sources"]["scholarship"]["text"]
            deposit_text = report["sources"]["deposit"]["text"]

            required = [
                "£2,000 in the first year",
                "£4,000 in the first year",
                "£1,000 in each subsequent year",
                "all conditions of registration",
                "Accredited Prior Certified Learning",
                "cannot be combined with any other scholarships",
            ]

            normalised = scholarship_text.casefold()

            for phrase in required:
                if phrase.casefold() not in normalised:
                    raise ValueError(
                        f"Scholarship source needs review: {phrase}"
                    )

            if (
                "writtle" not in normalised
                or "automatically get £2,000" not in normalised
            ):
                raise ValueError(
                    "The Writtle first-year award needs review."
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
                f"No database changes. Report: {output}"
            ) from exc

        with transaction.atomic():
            university = University.objects.select_for_update().get(
                university_id="UK003"
            )

            courses = list(
                Course.objects.select_for_update()
                .filter(
                    university=university,
                    course_id__in=IDS,
                    is_active=True,
                    level="UG",
                    start_year=2027,
                    start_month="September",
                )
            )

            if len(courses) != len(IDS):
                raise CommandError(
                    "Apply the reviewed 24-course tuition batch first. "
                    "One or more course records do not match."
                )

            tuitions = list(
                Tuition.objects.select_for_update().filter(
                    course_id__in=IDS,
                    residency="INTL",
                )
            )

            by_course = {}
            for tuition in tuitions:
                by_course.setdefault(tuition.course_id, []).append(tuition)

            sources = {}

            for course_id in IDS:
                rows = by_course.get(course_id, [])

                if len(rows) != 1 or rows[0].currency != "GBP":
                    raise CommandError(
                        f"{course_id}: exactly one GBP INTL "
                        "tuition is required."
                    )

                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=rows[0])
                    .first()
                )

                if (
                    source is None
                    or source.academic_year != ACADEMIC_YEAR
                    or not isinstance(source.raw_fees, dict)
                    or source.raw_fees.get("verified") is not True
                ):
                    raise CommandError(
                        f"{course_id}: verified 2027/28 tuition "
                        "source is required."
                    )

                sources[course_id] = source

            tiers = [
                ("lower", "2000.00", IDS),
                (
                    "higher",
                    "4000.00",
                    sorted(set(IDS) - set(WRITTLE_IDS)),
                ),
            ]

            for tier, amount, scope in tiers:
                name = (
                    "ARU International Undergraduate Scholarship "
                    f"— {tier.title()} — Year 1 ({ACADEMIC_YEAR})"
                )

                matches = list(
                    Scholarship.objects.select_for_update().filter(
                        university=university,
                        name=name,
                        level="UG",
                    )
                )

                if len(matches) > 1:
                    raise CommandError(
                        f"Duplicate scholarship records: {name}"
                    )

                award = matches[0] if matches else None
                existing_rule = (
                    ScholarshipRule.objects.select_for_update()
                    .filter(scholarship=award)
                    .first()
                    if award else None
                )

                fields = {
                    "award_type": "Tuition Reduction",
                    "award_value": amount,
                    "currency": "GBP",
                    "deadline": None,
                    "eligibility": (
                        "Eligible new international campus "
                        "undergraduate students entering Year 1."
                    ),
                    "requirements": (
                        "ARU-confirmed award tier; registration "
                        "conditions met; eligible funding; full-time; "
                        "no APCL adjustment required."
                    ),
                    "description": (
                        f"Conditional £{amount} first-year tuition "
                        "reduction. Later years and payment discounts "
                        "require separate rules."
                    ),
                    "url": SCHOLARSHIP_URL,
                }

                rules = rule_data(scope, amount, tier)

                before = {
                    "scholarship": snapshot(award, fields),
                    "rule": snapshot(
                        existing_rule,
                        ["verified", "source_url", "rules"],
                    ),
                }
                after = {
                    "scholarship": fields,
                    "rule": {
                        "verified": True,
                        "source_url": SCHOLARSHIP_URL,
                        "rules": rules,
                    },
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
                            level="UG",
                            **fields,
                        )
                    else:
                        for key, value in fields.items():
                            setattr(award, key, value)
                        award.save(update_fields=list(fields))

                    ScholarshipRule.objects.update_or_create(
                        scholarship=award,
                        defaults={
                            **after["rule"],
                            "reviewed_at": timezone.now(),
                        },
                    )

            deposit_rules = {
                "schema_version": 1,
                "kind": "fixed",
                "amount": "4000.00",
                "currency": "GBP",
                "fee_status": "INTL",
                "payment_stage": "before_cas",
                "course_ids": IDS,
                "course_kinds": ["undergraduate_taught"],
                "requires_student_visa": True,
                "full_fee_paying": True,
                "sponsorship_letter_requires_review": True,
                "deduct_scholarship_from_deposit": False,
                "offer_letter_confirmation_required": True,
                "source_period": "current_undated_policy",
                "academic_year": ACADEMIC_YEAR,
                "note": (
                    "Current published pre-CAS deposit: £4,000 "
                    "for full-fee-paying international taught "
                    "students. Confirm the requirement in the "
                    "individual offer. Sponsored arrangements "
                    "require separate assessment. Recheck the "
                    "current policy before payment."
                ),
                "eligibility_questions": [
                    {
                        "key": "requires_student_visa",
                        "question": "Do you require a Student visa?",
                        "type": "boolean",
                    },
                    {
                        "key": "full_fee_paying",
                        "question": (
                            "Are you a full-fee-paying international "
                            "student under ARU's payment arrangements?"
                        ),
                        "type": "boolean",
                    },
                    {
                        "key": "sponsored",
                        "question": (
                            "Will a sponsor provide a sponsorship letter?"
                        ),
                        "type": "boolean",
                    },
                ],
            }

            policy = (
                DepositPolicy.objects.select_for_update()
                .filter(
                    university=university,
                    academic_year=ACADEMIC_YEAR,
                )
                .first()
            )

            policy_fields = {
                "currency": "GBP",
                "rules": deposit_rules,
                "source_url": DEPOSIT_URL,
                "verified": True,
            }

            policy_before = snapshot(policy, policy_fields)
            policy_changed = policy_before != policy_fields

            report["proposals"].append({
                "type": "deposit_policy",
                "changed": policy_changed,
                "before": policy_before,
                "after": policy_fields,
            })

            if options["apply"] and policy_changed:
                DepositPolicy.objects.update_or_create(
                    university=university,
                    academic_year=ACADEMIC_YEAR,
                    defaults={
                        **policy_fields,
                        "source_text": deposit_text,
                        "source_sha256": (
                            report["sources"]["deposit"]["sha256"]
                        ),
                        "checked_at": timezone.now(),
                    },
                )

            for course_id in IDS:
                tuition = by_course[course_id][0]
                source = sources[course_id]

                old_raw = copy.deepcopy(source.raw_fees)
                new_raw = {
                    **old_raw,
                    "scholarship_status": (
                        "verified_rule_eligibility_required"
                    ),
                    "deposit_status": (
                        "verified_current_policy_offer_confirmation_required"
                    ),
                    "unverified_legacy_discount_disabled": True,
                }

                before = {
                    "scholarship": str(tuition.scholarship),
                    "scholarship_source_id": (
                        tuition.scholarship_source_id
                    ),
                    "raw_fees": old_raw,
                }
                after = {
                    "scholarship": "0.00",
                    "scholarship_source_id": None,
                    "raw_fees": new_raw,
                }

                changed = (
                    tuition.scholarship != 0
                    or tuition.scholarship_source_id is not None
                    or old_raw != new_raw
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
            "£4,000 deposit policy: "
            f"{'update' if policy_changed else 'unchanged'}"
        )
        self.stdout.write(
            "Proposed/updated items: "
            f"{sum(p['changed'] for p in report['proposals'])}"
        )
        self.stdout.write(f"Report: {output}")

        if options["apply"]:
            self.stdout.write(
                "Scholarship rules, deposit policy and "
                "course review metadata saved."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )

        self.stdout.write(
            "Gross tuition and numeric stored deposits unchanged. "
            "Scholarship deductions require confirmed eligibility."
        )