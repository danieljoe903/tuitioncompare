import hashlib
import json
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from app.models import (
    Course,
    DepositPolicy,
    ScholarshipRule,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
)


UG_URL = (
    "https://www.aru.ac.uk/international/international-scholarships/"
    "international-undergraduate-scholarship"
)

DEPOSIT_URL = (
    "https://www.aru.ac.uk/international/how-to-apply/"
    "step-4---receive-our-decision-on-your-application"
)

PG_URL = (
    "https://www.aru.ac.uk/international/international-scholarships/"
    "international-postgraduate-scholarship"
)

ALUMNI_URL = (
    "https://www.aru.ac.uk/student-life/preparing-for-study/"
    "help-with-finances/scholarships/alumni-scholarship"
)

GAMES = "C_UK003_65"
MPHIL = "C_ARU003_RES_01"
ANIMATION = "C_UK003_94"

EXPECTED = {
    GAMES: {
        "title": "BSc (Hons) Computer Games Programming",
        "year": "2027/28",
        "amount": "18825.00",
        "kind": "undergraduate_taught",
    },
    MPHIL: {
        "title": "MPhil Allied Health and Social Care",
        "year": "2026/27",
        "amount": "17500.00",
        "kind": "postgraduate_research",
    },
    ANIMATION: {
        "title": "BA (Hons) 3D Animation",
        "year": "",
        "amount": None,
        "kind": "undergraduate_taught",
    },
}


def money(value):
    amount = Decimal(str(value))

    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid monetary amount.")

    return format(amount.quantize(Decimal("0.01")), ".2f")


class Command(BaseCommand):
    help = (
        "Reconcile Games Programming scholarships/deposit and save "
        "MPhil/Animation applicability reviews."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Save the reviewed rules, scopes and metadata.",
        )

    def collect_sources(self):
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )

        collected = {}

        with requests.Session() as session:
            session.headers["User-Agent"] = (
                "TuitionComparePolicyInspector/1.0"
            )

            session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )

            for url in [UG_URL, DEPOSIT_URL, PG_URL, ALUMNI_URL]:
                self.stdout.write(f"Reading: {url}")

                response = session.get(
                    url,
                    timeout=(30, 60),
                )
                response.raise_for_status()

                if response.url.rstrip("/") != url.rstrip("/"):
                    raise ValueError(
                        f"Unexpected redirect: {response.url}"
                    )

                soup = BeautifulSoup(
                    response.text,
                    "html.parser",
                )

                root = soup.find("main") or soup

                for element in root.find_all(
                    ["script", "style", "nav", "footer"]
                ):
                    element.decompose()

                text = " ".join(
                    root.get_text(" ", strip=True).split()
                )

                collected[url] = {
                    "source_url": response.url,
                    "text": text,
                    "text_sha256": hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                }

        self.verify_sources(collected)
        return collected

    def verify_sources(self, sources):
        checks = {
            UG_URL: [
                r"International Undergraduate Scholarship",
                r"£2,000 in the first year",
                r"£4,000 in the first year",
                r"starting in Year 1",
                r"Cambridge",
                r"Accredited Prior Certified Learning",
                r"conditions of registration",
            ],
            DEPOSIT_URL: [
                r"full fee paying international students",
                r"before being issued with a CAS",
                (
                    r"deposit for Undergraduate and Postgraduate "
                    r"taught courses is £4,000"
                ),
                r"offer letter",
            ],
            PG_URL: [
                r"Up to £4,000",
                r"studying for an MBA or be an ARU graduate",
            ],
            ALUMNI_URL: [
                r"£400 discount",
                r"postgraduate research",
                r"2026-27 academic year",
                r"from the UK or an EU/EEA student",
                r"International students should see",
            ],
        }

        for url, patterns in checks.items():
            text = sources[url]["text"]

            for pattern in patterns:
                if not re.search(
                    pattern,
                    text,
                    re.IGNORECASE,
                ):
                    raise ValueError(
                        "Published policy changed or could not be "
                        f"verified: {url}; missing {pattern!r}"
                    )

    @staticmethod
    def add_update(updates, model_name, obj, after):
        before = {
            field: deepcopy(getattr(obj, field))
            for field in after
        }

        if before != after:
            updates.append({
                "model": model_name,
                "id": obj.pk,
                "before": before,
                "after": deepcopy(after),
            })

    def prepare(self, sources):
        updates = []
        records = {}

        for course_id, expected in EXPECTED.items():
            course = Course.objects.select_for_update().get(
                university_id="UK003",
                course_id=course_id,
            )

            if course.title != expected["title"]:
                raise CommandError(
                    f"{course_id}: apply its replacement first."
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
                raise CommandError(
                    f"{course_id}: expected one GBP "
                    "international tuition."
                )

            tuition = tuitions[0]

            source = TuitionSource.objects.select_for_update().get(
                tuition=tuition
            )

            raw = source.raw_fees

            if not isinstance(raw, dict):
                raise CommandError(
                    f"{course_id}: invalid source metadata."
                )

            if raw.get("course_kind") != expected["kind"]:
                raise CommandError(
                    f"{course_id}: unexpected course kind."
                )

            if expected["amount"] is not None:
                if (
                    source.academic_year != expected["year"]
                    or raw.get("academic_year") != expected["year"]
                    or raw.get("verified") is not True
                    or money(tuition.tuition_fee)
                    != expected["amount"]
                    or money(raw.get("annual_tuition"))
                    != expected["amount"]
                ):
                    raise CommandError(
                        f"{course_id}: saved verified fee/year "
                        "does not match the reviewed route."
                    )

            elif (
                raw.get("verified") is not False
                or source.fee_basis != "unavailable"
            ):
                raise CommandError(
                    "Animation must retain unavailable "
                    "international tuition."
                )

            records[course_id] = {
                "tuition": tuition,
                "source": source,
            }

        rules = list(
            ScholarshipRule.objects.select_for_update().filter(
                scholarship__university_id="UK003"
            )
        )

        selected = {}

        for tier, value in [
            ("lower", "2000.00"),
            ("higher", "4000.00"),
        ]:
            matches = []

            for rule in rules:
                data = rule.rules

                if not isinstance(data, dict):
                    continue

                conditions = data.get("conditions", {})

                if not isinstance(conditions, dict):
                    continue

                if (
                    rule.verified
                    and rule.source_url.rstrip("/")
                    == UG_URL.rstrip("/")
                    and data.get("academic_year") == "2027/28"
                    and data.get("kind") == "fixed"
                    and data.get("currency") == "GBP"
                    and money(data.get("value")) == value
                    and conditions.get("aru_ug_award_tier")
                    == [tier]
                ):
                    matches.append(rule)

            if len(matches) != 1:
                raise CommandError(
                    f"Expected one verified {tier} UG rule "
                    "for 2027/28."
                )

            rule = matches[0]
            data = deepcopy(rule.rules)
            conditions = data["conditions"]

            required = {
                "fee_status": ["INTL"],
                "new_entrant": [True],
                "full_time": [True],
                "entry_year": [1],
                "apcl": [False],
                "registration_conditions_met": [True],
                "aru_funding_eligibility_confirmed": [True],
                "aru_ug_award_confirmed": [True],
            }

            for key, value_required in required.items():
                if conditions.get(key) != value_required:
                    raise CommandError(
                        f"{tier} UG rule: unexpected condition {key}."
                    )

            if not data.get("eligibility_questions"):
                raise CommandError(
                    f"{tier} UG rule has no saved eligibility questions."
                )

            scope = conditions.get("course_id")

            if not isinstance(scope, list) or not all(
                isinstance(value, str) for value in scope
            ):
                raise CommandError(
                    f"{tier} UG rule has an invalid course scope."
                )

            if GAMES not in scope:
                conditions["course_id"] = sorted(
                    set(scope) | {GAMES}
                )

            self.add_update(
                updates,
                "ScholarshipRule",
                rule,
                {"rules": data},
            )

            selected[tier] = {
                "id": rule.pk,
                "scope_count": len(conditions["course_id"]),
                "questions": deepcopy(
                    data["eligibility_questions"]
                ),
            }

        # Do not leave these pending routes inside an existing
        # verified scholarship scope by accident.
        for rule in rules:
            data = rule.rules

            if not isinstance(data, dict):
                continue

            conditions = data.get("conditions", {})

            if not isinstance(conditions, dict):
                continue

            scope = conditions.get("course_id", [])

            if isinstance(scope, list) and (
                MPHIL in scope or ANIMATION in scope
            ):
                raise CommandError(
                    "A pending route already belongs to scholarship "
                    f"rule {rule.pk}. Review that scope first."
                )

        policies = list(
            DepositPolicy.objects.select_for_update().filter(
                university_id="UK003",
                currency="GBP",
            )
        )

        matches = [
            policy
            for policy in policies
            if policy.academic_year == "2027/28"
            and policy.verified
        ]

        if len(matches) != 1:
            raise CommandError(
                "Expected one verified GBP deposit policy "
                "for 2027/28."
            )

        policy = matches[0]
        deposit_rules = deepcopy(policy.rules)

        if (
            not isinstance(deposit_rules, dict)
            or deposit_rules.get("kind") != "fixed"
            or deposit_rules.get("academic_year") != "2027/28"
            or deposit_rules.get("currency") != "GBP"
            or money(deposit_rules.get("amount")) != "4000.00"
            or deposit_rules.get("fee_status") != "INTL"
            or deposit_rules.get("payment_stage") != "before_cas"
            or deposit_rules.get("requires_student_visa") is not True
            or deposit_rules.get("full_fee_paying") is not True
            or deposit_rules.get(
                "deduct_scholarship_from_deposit"
            ) is not False
            or policy.source_url.rstrip("/")
            != DEPOSIT_URL.rstrip("/")
        ):
            raise CommandError(
                "The saved deposit policy requires review."
            )

        scope = deposit_rules.get("course_ids")

        if not isinstance(scope, list):
            raise CommandError("Invalid deposit course scope.")

        if "undergraduate_taught" not in deposit_rules.get(
            "course_kinds", []
        ):
            raise CommandError(
                "The deposit policy does not cover taught UG routes."
            )

        if not deposit_rules.get("eligibility_questions"):
            raise CommandError(
                "The deposit policy has no eligibility questions."
            )

        for saved_policy in policies:
            data = saved_policy.rules

            if isinstance(data, dict):
                saved_scope = data.get("course_ids", [])

                if isinstance(saved_scope, list) and (
                    MPHIL in saved_scope
                    or ANIMATION in saved_scope
                ):
                    raise CommandError(
                        "MPhil or Animation already belongs to a "
                        "deposit scope. Review it before continuing."
                    )

        if GAMES not in scope:
            deposit_rules["course_ids"] = sorted(
                set(scope) | {GAMES}
            )

        self.add_update(
            updates,
            "DepositPolicy",
            policy,
            {"rules": deposit_rules},
        )

        for course_id, record in records.items():
            source = record["source"]
            raw = deepcopy(source.raw_fees)

            if course_id == GAMES:
                scholarship_review = {
                    "status": "verified_rule_eligibility_required",
                    "academic_year": "2027/28",
                    "source_url": UG_URL,
                    "note": (
                        "First-year lower award £2,000 or higher "
                        "award £4,000, subject to the saved eligibility "
                        "conditions and ARU assessment. Awards are "
                        "alternatives and are not added together. "
                        "The current policy is undated and is associated "
                        "with this reviewed 2027/28 route."
                    ),
                    "eligibility_questions": deepcopy(
                        selected["lower"]["questions"]
                    ),
                }

                deposit_review = {
                    "status": "verified_rule_eligibility_required",
                    "policy_academic_year": "2027/28",
                    "source_url": DEPOSIT_URL,
                    "note": (
                        "Current fixed pre-CAS deposit £4,000, "
                        "subject to visa, full-fee-paying and "
                        "sponsorship assessment. Confirm the offer. "
                        "Scholarships do not reduce this fixed deposit. "
                        "The published policy is undated."
                    ),
                    "eligibility_questions": deepcopy(
                        deposit_rules["eligibility_questions"]
                    ),
                }

            elif course_id == MPHIL:
                scholarship_review = {
                    "status": "applicability_unconfirmed",
                    "academic_year": "2026/27",
                    "source_url": PG_URL,
                    "reviewed_source_urls": [PG_URL, ALUMNI_URL],
                    "note": (
                        "No international MPhil scholarship deduction "
                        "has been verified for January 2027 / 2026/27. "
                        "The £400 Alumni Scholarship has UK/eligible "
                        "EU restrictions. The international PG page "
                        "does not establish this MPhil's eligibility "
                        "and applicable award amount."
                    ),
                    "eligibility_questions": [{
                        "key": "aru_graduate",
                        "type": "boolean",
                        "question": "Are you an ARU graduate?",
                        "note": (
                            "An answer alone does not verify a "
                            "research scholarship or permit deduction."
                        ),
                    }],
                }

                deposit_review = {
                    "status": "applicability_unconfirmed",
                    "academic_year": "2026/27",
                    "source_url": DEPOSIT_URL,
                    "note": (
                        "The published £4,000 rule expressly covers "
                        "taught courses. A research deposit amount "
                        "has not been verified for this MPhil. "
                        "Ask ARU to confirm the requirement in "
                        "the individual offer."
                    ),
                    "eligibility_questions": [],
                }

            else:
                scholarship_review = {
                    "status": "applicability_unconfirmed",
                    "academic_year": "2027/28",
                    "source_url": UG_URL,
                    "note": (
                        "International fee and application applicability "
                        "for the reviewed 3D Animation route remain "
                        "unconfirmed. Do not deduct an award or "
                        "interpret this as proof that no award exists."
                    ),
                    "eligibility_questions": [],
                }

                deposit_review = {
                    "status": "applicability_unconfirmed",
                    "academic_year": "2027/28",
                    "source_url": DEPOSIT_URL,
                    "note": (
                        "International route applicability remains "
                        "unconfirmed. Do not extend the fixed pre-CAS "
                        "policy until the route's applicability "
                        "has been verified."
                    ),
                    "eligibility_questions": [],
                }

            raw["scholarship_status"] = scholarship_review["status"]
            raw["scholarship_review"] = scholarship_review
            raw["deposit_status"] = deposit_review["status"]
            raw["deposit_review"] = deposit_review

            self.add_update(
                updates,
                "TuitionSource",
                source,
                {"raw_fees": raw},
            )

        summary = {
            "lower_ug_scope": selected["lower"]["scope_count"],
            "higher_ug_scope": selected["higher"]["scope_count"],
            "deposit_scope": len(deposit_rules["course_ids"]),
            "games_net_if_lower_eligible": "16825.00",
            "games_net_if_higher_eligible": "14825.00",
            "games_conditional_deposit": "4000.00",
            "mphil_scholarship": "pending_2026/27",
            "mphil_deposit": "pending_2026/27",
            "animation_scholarship": "pending",
            "animation_deposit": "pending",
        }

        return updates, summary

    def apply_updates(self, updates):
        models = {
            "ScholarshipRule": ScholarshipRule,
            "DepositPolicy": DepositPolicy,
            "TuitionSource": TuitionSource,
        }

        for update in updates:
            model = models[update["model"]]
            obj = model.objects.get(pk=update["id"])

            current = {
                field: deepcopy(getattr(obj, field))
                for field in update["before"]
            }

            if current != update["before"]:
                raise CommandError(
                    "Database state changed during reconciliation."
                )

            for field, value in update["after"].items():
                setattr(obj, field, deepcopy(value))

            fields = list(update["after"])

            if update["model"] == "ScholarshipRule":
                obj.reviewed_at = timezone.now()
                fields.append("reviewed_at")

            elif update["model"] == "TuitionSource":
                fields.append("checked_at")

            elif update["model"] == "DepositPolicy":
                fields.append("updated_at")

            obj.save(update_fields=fields)

            if update["model"] == "TuitionSource":
                TuitionUpdateAudit.objects.create(
                    tuition_id=obj.tuition_id,
                    course_identifier=obj.tuition.course_id,
                    residency="INTL",
                    before=update["before"],
                    after=update["after"],
                )

    @staticmethod
    def write_report(path, data):
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

    def handle(self, *args, **options):
        try:
            sources = self.collect_sources()
        except (
            requests.RequestException,
            ValueError,
            TypeError,
        ) as exc:
            raise CommandError(
                f"Source verification stopped: {exc}. "
                "No database records changed."
            ) from exc

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        folder = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aru"
            / f"games-health-awards-{stamp}"
        )
        output = folder / "reconciliation.json"

        # The report is written before any mutation. Failure to save
        # that preview prevents database changes.
        with transaction.atomic():
            try:
                updates, summary = self.prepare(sources)
            except (
                ValueError,
                TypeError,
                KeyError,
                ArithmeticError,
                Course.DoesNotExist,
                TuitionSource.DoesNotExist,
            ) as exc:
                raise CommandError(
                    f"Saved-rule verification stopped: {exc}. "
                    "No database records changed."
                ) from exc

            report = {
                "generated_at": timezone.now().isoformat(),
                "university_id": "UK003",
                "mode": "preview",
                "database_changes": False,
                "sources": sources,
                "summary": summary,
                "updates": updates,
            }

            try:
                self.write_report(output, report)
            except OSError as exc:
                raise CommandError(
                    f"Cannot save preview report: {exc}. "
                    "No database records changed."
                ) from exc

            if options["apply"]:
                self.apply_updates(updates)

        if options["apply"]:
            report["mode"] = "apply"
            report["database_changes"] = bool(updates)

            try:
                self.write_report(output, report)
            except OSError as exc:
                raise CommandError(
                    "Database changes were committed, but the "
                    f"final JSON report could not be updated: {exc}"
                ) from exc

        self.stdout.write(
            f"Lower UG scholarship scope: "
            f"{summary['lower_ug_scope']} courses"
        )
        self.stdout.write(
            f"Higher UG scholarship scope: "
            f"{summary['higher_ug_scope']} courses"
        )
        self.stdout.write(
            f"2027/28 deposit scope: "
            f"{summary['deposit_scope']} courses"
        )
        self.stdout.write(
            "Games Programming: conditional £2,000/£4,000 award; "
            "net £16,825/£14,825 if eligible. Deposit £4,000 "
            "subject to payment eligibility."
        )
        self.stdout.write(
            "MPhil: international scholarship and research deposit "
            "remain pending for 2026/27."
        )
        self.stdout.write(
            "Animation: fee, scholarship and deposit applicability "
            "remain unconfirmed."
        )
        self.stdout.write(
            f"Proposed/updated items: {len(updates)}"
        )
        self.stdout.write(f"Report: {output}")

        if options["apply"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "Scholarship scopes, deposit scope and "
                    "review metadata saved."
                )
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )

        self.stdout.write(
            "Eligibility questions preserved. Gross tuition and "
            "numeric stored discounts/deposits unchanged. "
            "The comparison calculates eligible deductions "
            "from the saved rules."
        )