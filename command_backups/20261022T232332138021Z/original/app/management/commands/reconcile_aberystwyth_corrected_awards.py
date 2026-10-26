import copy
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
    University,
)

from .import_aberystwyth_corrected_fees import FEES
from .correct_aberystwyth_remaining_courses import MATCHES
from .reconcile_aberystwyth_awards import (
    SCHOLARSHIP_URL,
    download,
    parse_scholarships,
)


PLANS = [
    {
        "level": "UG",
        "band": "Humanities",
        "verified": True,
        "value": "3000.00",
        "course_ids": [
            "C_UK002_10",
            "C_UK002_41",
            "C_UK002_60",
        ],
    },
    {
        "level": "PG",
        "band": "Humanities",
        "verified": True,
        "value": "3000.00",
        "course_ids": ["C_ABE002_RES_04"],
    },
    {
        "level": "UG",
        "band": "Sciences",
        "verified": False,
        "value": "4500.00",
        "course_ids": ["C_UK002_84"],
    },
]

PENDING = {
    "C_ABE002_RES_02": (
        "MRes programme eligibility under the published Global Citizens "
        "award categories has not been explicitly confirmed."
    ),
    "C_ABE002_RES_06": (
        "January 2027 is in 2026/27. Global Citizens starts from "
        "2027/28; other scholarship applicability remains unconfirmed."
    ),
}


class Command(BaseCommand):
    help = (
        "Extend saved scholarship rules to accepted corrected courses "
        "and record unresolved applicability for two research programmes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
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
                    "https://",
                    HTTPAdapter(max_retries=retry),
                )
                html, main = download(session, SCHOLARSHIP_URL)
                published, _content = parse_scholarships(main)

        except (
            requests.RequestException,
            UnicodeDecodeError,
            ValueError,
            ArithmeticError,
        ) as exc:
            raise CommandError(
                f"Scholarship source verification stopped: {exc}"
            ) from exc

        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        directory = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aberystwyth"
            / f"corrected-awards-{stamp}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "scholarship.html").write_bytes(html)

        rule_proposals = []
        source_proposals = []
        locked_sources = {}
        locked_rules = {}
        decisions = {}

        with transaction.atomic():
            university = University.objects.select_for_update().get(
                pk="UK002",
                country="UK",
            )

            if university.name.strip().casefold() != "aberystwyth university":
                raise CommandError("University identity mismatch.")

            # Require all seven accepted identities and imported fees.
            for course_id, details in FEES.items():
                year, group, band, expected_fee, kind = details
                match = MATCHES[course_id]

                records = list(
                    Tuition.objects.select_for_update()
                    .select_related("course")
                    .filter(
                        course_id=course_id,
                        course__university=university,
                        course__is_active=True,
                        residency="INTL",
                    )
                )

                if len(records) != 1:
                    raise CommandError(
                        f"{course_id}: unique international tuition required."
                    )

                tuition = records[0]
                course = tuition.course
                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )
                raw = source.raw_fees if source else None
                identity = (
                    raw.get("course_identity", {})
                    if isinstance(raw, dict) else {}
                )

                if (
                    course.title != match[3]
                    or course.level != match[4]
                    or course.duration != match[5]
                    or course.start_month != match[6]
                    or course.start_year != match[7]
                    or source is None
                    or source.academic_year != year
                    or source.fee_basis != "annual_tuition"
                    or not isinstance(raw, dict)
                    or raw.get("verified") is not True
                    or raw.get("fee_band") != band
                    or identity.get("status")
                    != "accepted_current_catalogue_match"
                    or identity.get("official_code") != match[1]
                    or tuition.currency != "GBP"
                    or Decimal(str(tuition.tuition_fee))
                    != Decimal(expected_fee)
                ):
                    raise CommandError(
                        f"{course_id}: apply the corrected tuition import "
                        "first, or review changed course evidence."
                    )

                locked_sources[course_id] = (tuition, source)

            # Extend existing scopes without dropping earlier courses.
            for plan in PLANS:
                level = plan["level"]
                band = plan["band"]
                name = (
                    f"Global Citizens Scholarship — "
                    f"{band} {level} (2027/28)"
                )

                awards = list(
                    Scholarship.objects.select_for_update().filter(
                        university=university,
                        name=name,
                        level=level,
                    )
                )

                if len(awards) != 1:
                    raise CommandError(
                        f"Unique existing scholarship required: {name}"
                    )

                rule = (
                    ScholarshipRule.objects.select_for_update()
                    .filter(scholarship=awards[0])
                    .first()
                )

                if (
                    rule is None
                    or rule.source_url != SCHOLARSHIP_URL
                    or rule.verified is not plan["verified"]
                    or rule.rules.get("academic_year") != "2027/28"
                    or rule.rules.get("kind") != "fixed"
                    or rule.rules.get("study_years") != [1]
                    or Decimal(str(rule.rules.get("value")))
                    != Decimal(plan["value"])
                    or Decimal(published[(level, band)]["award"])
                    != Decimal(plan["value"])
                ):
                    raise CommandError(
                        f"{name}: published or saved rules require review."
                    )

                before = copy.deepcopy(rule.rules)
                after = copy.deepcopy(before)
                ids = after.get("conditions", {}).get("course_id")

                if not isinstance(ids, list) or not all(
                    isinstance(value, str) for value in ids
                ):
                    raise CommandError(f"{name}: invalid course scope.")

                after["conditions"]["course_id"] = sorted(
                    set(ids) | set(plan["course_ids"])
                )

                rule_proposals.append({
                    "name": name,
                    "rule_id": rule.pk,
                    "verified": plan["verified"],
                    "scope_count": len(after["conditions"]["course_id"]),
                    "changed": before != after,
                    "before": before,
                    "after": after,
                })
                locked_rules[rule.pk] = rule

                for course_id in plan["course_ids"]:
                    decisions[course_id] = {
                        "status": (
                            "verified_rule_eligibility_required"
                            if plan["verified"]
                            else "pending_fee_source_discrepancy"
                        ),
                        "rule_id": rule.pk,
                        "reason": (
                            "Existing verified conditional award extended "
                            "to the accepted course and fee year."
                            if plan["verified"]
                            else "Undergraduate science tuition differs "
                            "between official fee and scholarship pages. "
                            "The award remains unverified and unapplied."
                        ),
                    }

            for course_id, reason in PENDING.items():
                decisions[course_id] = {
                    "status": "applicability_unconfirmed",
                    "rule_id": None,
                    "reason": reason,
                }

            for course_id, decision in decisions.items():
                tuition, source = locked_sources[course_id]
                before = copy.deepcopy(source.raw_fees)
                after = copy.deepcopy(before)

                after["scholarship_status"] = decision["status"]
                after["scholarship_review"] = {
                    **decision,
                    "source_url": SCHOLARSHIP_URL,
                }

                notes = after.get("display_notes")
                if isinstance(notes, dict):
                    notes["scholarship"] = (
                        "A scholarship estimate is available when "
                        "the eligibility conditions are confirmed."
                        if decision["status"]
                        == "verified_rule_eligibility_required"
                        else "Scholarship information is not confirmed for "
                        "this course and intake. No unverified discount "
                        "has been included."
                    )

                source_proposals.append({
                    "course_id": course_id,
                    "changed": before != after,
                    "before": before,
                    "after": after,
                })

            output = directory / "reconciliation.json"
            output.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "university_id": "UK002",
                        "mode": "apply" if options["apply"] else "preview",
                        "source_url": SCHOLARSHIP_URL,
                        "source_sha256": hashlib.sha256(html).hexdigest(),
                        "scholarship_rules": rule_proposals,
                        "course_reviews": source_proposals,
                        "tuition_prices_changed": False,
                        "stored_discounts_changed": False,
                        "deposit_amounts_changed": False,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            if options["apply"]:
                for proposal in rule_proposals:
                    if not proposal["changed"]:
                        continue

                    rule = locked_rules[proposal["rule_id"]]
                    rule.rules = proposal["after"]
                    rule.reviewed_at = now
                    rule.save(update_fields=["rules", "reviewed_at"])

                # Audit rule-scope changes alongside the affected course.
                changed_rule_ids = {
                    proposal["rule_id"]
                    for proposal in rule_proposals
                    if proposal["changed"]
                }

                for proposal in source_proposals:
                    course_id = proposal["course_id"]
                    tuition, source = locked_sources[course_id]
                    decision = decisions[course_id]

                    rule_changes = [
                        item for item in rule_proposals
                        if item["rule_id"] == decision["rule_id"]
                        and item["rule_id"] in changed_rule_ids
                    ]

                    if proposal["changed"]:
                        source.raw_fees = proposal["after"]
                        source.save(update_fields=["raw_fees"])

                    if proposal["changed"] or rule_changes:
                        TuitionUpdateAudit.objects.create(
                            tuition=tuition,
                            course_identifier=course_id,
                            residency="INTL",
                            before={
                                "raw_fees": proposal["before"],
                                "scholarship_rules": [
                                    {
                                        "rule_id": item["rule_id"],
                                        "rules": item["before"],
                                    }
                                    for item in rule_changes
                                ],
                            },
                            after={
                                "raw_fees": proposal["after"],
                                "scholarship_rules": [
                                    {
                                        "rule_id": item["rule_id"],
                                        "rules": item["after"],
                                    }
                                    for item in rule_changes
                                ],
                            },
                        )

        for proposal in rule_proposals:
            self.stdout.write(
                f"{proposal['name']}: "
                f"{proposal['scope_count']} total courses; "
                f"{'verified' if proposal['verified'] else 'REVIEW REQUIRED'}"
                f" — {'update' if proposal['changed'] else 'unchanged'}"
            )

        for course_id, decision in decisions.items():
            self.stdout.write(
                f"{course_id}: {decision['status']}"
            )

        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Scholarship scopes and course review metadata saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Tuition prices, stored discounts and deposit amounts unchanged."
        )