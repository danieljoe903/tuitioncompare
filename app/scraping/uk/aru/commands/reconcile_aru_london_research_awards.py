import copy
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
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


LONDON_IDS = {
    "C_UK003_19",
    "C_UK003_24",
    "C_UK003_29",
    "C_UK003_96",
}

PUBLIC_HEALTH = "C_UK003_26"
RESEARCH = "C_ARU003_RES_07"
ALL_IDS = LONDON_IDS | {PUBLIC_HEALTH, RESEARCH}

SCHOLARSHIP_URL = (
    "https://london.aru.ac.uk/admissions/scholarships"
)

QUESTIONS = [
    {
        "key": "aru_london_alumni",
        "type": "boolean",
        "label": "Did you complete a degree at ARU London?",
        "required": True,
    },
    {
        "key": "uk_first_class_degree",
        "type": "boolean",
        "label": (
            "Have you achieved a first-class honours degree "
            "from a UK university?"
        ),
        "required": True,
    },
    {
        "key": "aru_london_award_confirmed",
        "type": "boolean",
        "label": (
            "Has ARU London confirmed your scholarship "
            "for this course and intake?"
        ),
        "required": True,
    },
]


class Command(BaseCommand):
    help = (
        "Reconcile London/research award review metadata and "
        "extend the existing deposit policy to four London courses."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Save the validated reconciliation.",
        )

    def collect_policy(self, folder):
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )

        with requests.Session() as session:
            session.headers["User-Agent"] = (
                "UniversityTuitionCompareSourceInspector/1.0"
            )
            session.mount(
                "https://", HTTPAdapter(max_retries=retry)
            )

            self.stdout.write(f"Reading: {SCHOLARSHIP_URL}")
            response = session.get(
                SCHOLARSHIP_URL,
                timeout=(30, 60),
            )
            response.raise_for_status()

        if (
            response.url.rstrip("/")
            != SCHOLARSHIP_URL.rstrip("/")
        ):
            raise CommandError(
                f"Unexpected policy redirect: {response.url}"
            )

        soup = BeautifulSoup(response.content, "html.parser")
        for element in soup(["script", "style"]):
            element.decompose()

        text = " ".join(
            soup.get_text(" ", strip=True).split()
        )
        lower = text.casefold()

        start = lower.find("masters scholarship programme")
        end = lower.find("aru alumni scholarship", start)

        if start < 0 or end <= start:
            raise CommandError(
                "Cannot isolate the Master's Scholarship policy."
            )

        masters_section = lower[start:end]

        if (
            "50%" not in masters_section
            or (
                "this scholarship is not available "
                "for international students."
            ) not in masters_section
            or "£2,000" not in text
            or "£1,000" not in text
            or "first class honours degree" not in lower
        ):
            raise CommandError(
                "Scholarship policy changed; manual review required."
            )

        (folder / "london-scholarships.html").write_bytes(
            response.content
        )

        return {
            "source_url": response.url,
            "sha256": hashlib.sha256(
                response.content
            ).hexdigest(),
        }

    def handle(self, *args, **options):
        folder = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aru"
            / (
                "london-research-awards-"
                + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            )
        )
        folder.mkdir(parents=True, exist_ok=False)

        try:
            policy_evidence = self.collect_policy(folder)
        except requests.RequestException as exc:
            raise CommandError(
                f"Policy collection failed: {exc}. "
                "No database records changed."
            ) from exc

        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
            "policy_evidence": policy_evidence,
            "courses": [],
            "deposit_policy": None,
        }

        with transaction.atomic():
            courses = {
                course.course_id: course
                for course in (
                    Course.objects.select_for_update()
                    .filter(
                        university_id="UK003",
                        course_id__in=ALL_IDS,
                        is_active=True,
                    )
                    .order_by("course_id")
                )
            }

            if set(courses) != ALL_IDS:
                raise CommandError(
                    "Expected all six active reviewed courses."
                )

            tuitions = list(
                Tuition.objects.select_for_update()
                .filter(
                    course_id__in=ALL_IDS,
                    residency="INTL",
                )
                .order_by("pk")
            )

            by_course = {}
            for tuition in tuitions:
                if tuition.course_id in by_course:
                    raise CommandError(
                        "Duplicate international tuition found."
                    )
                by_course[tuition.course_id] = tuition

            if (
                set(by_course) != ALL_IDS
                or any(t.currency != "GBP" for t in tuitions)
            ):
                raise CommandError(
                    "Each course requires one GBP international tuition."
                )

            sources = {
                source.tuition_id: source
                for source in (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition__in=tuitions)
                    .order_by("pk")
                )
            }

            # This batch must not inherit an unrelated main-campus
            # scholarship merely because it uses the same university ID.
            rules = (
                ScholarshipRule.objects.select_for_update()
                .filter(
                    scholarship__university_id="UK003",
                    verified=True,
                )
                .order_by("pk")
            )

            for rule in rules:
                data = rule.rules
                if not isinstance(data, dict):
                    raise CommandError(
                        "A verified ARU scholarship rule is malformed."
                    )

                conditions = data.get("conditions", {})
                scope = (
                    conditions.get("course_id", [])
                    if isinstance(conditions, dict)
                    else []
                )

                if not isinstance(scope, list):
                    raise CommandError(
                        "A verified ARU award scope requires review."
                    )

                if set(scope) & ALL_IDS:
                    raise CommandError(
                        "An existing verified scholarship already "
                        "covers this batch. Review it before proceeding."
                    )

            plans = []

            for course_id in sorted(ALL_IDS):
                tuition = by_course[course_id]
                source = sources.get(tuition.pk)
                course = courses[course_id]

                if source is None or not isinstance(
                    source.raw_fees, dict
                ):
                    raise CommandError(
                        f"{course_id}: source metadata is required."
                    )

                raw = source.raw_fees

                if course_id in LONDON_IDS:
                    if (
                        course.location != "ARU London"
                        or source.academic_year != "2027/28"
                        or raw.get("verified") is not True
                        or Decimal(str(tuition.tuition_fee))
                        != Decimal("20925.00")
                    ):
                        raise CommandError(
                            f"{course_id}: apply the reviewed "
                            "London fee import first."
                        )

                    deposit = raw.get(
                        "published_deposit_evidence", {}
                    )
                    if (
                        deposit.get("amount") != "4000.00"
                        or deposit.get("currency") != "GBP"
                        or deposit.get("alternative")
                        != "sponsorship_letter"
                    ):
                        raise CommandError(
                            f"{course_id}: course-specific "
                            "deposit evidence is missing."
                        )

                elif course_id == PUBLIC_HEALTH:
                    if (
                        course.location != "ARU London"
                        or raw.get("verified") is not False
                    ):
                        raise CommandError(
                            "Public Health must retain its reviewed "
                            "unavailable international-fee status."
                        )

                elif (
                    source.academic_year != "2026/27"
                    or raw.get("verified") is not True
                    or course.start_month != "January"
                    or course.start_year != 2027
                    or Decimal(str(tuition.tuition_fee))
                    != Decimal("17500.00")
                ):
                    raise CommandError(
                        "Apply the reviewed Education MPhil fee first."
                    )

                before = copy.deepcopy(raw)
                after = copy.deepcopy(raw)

                if course_id == RESEARCH:
                    after["scholarship_status"] = (
                        "research_applicability_unconfirmed"
                    )
                    after["deposit_status"] = (
                        "research_deposit_unconfirmed"
                    )
                    after["award_review"] = {
                        "status": "pending",
                        "note": (
                            "No applicable Education MPhil scholarship "
                            "deduction or research deposit amount has "
                            "been verified for January 2027. "
                            "Confirm with ARU."
                        ),
                    }
                else:
                    after["scholarship_status"] = (
                        "london_international_applicability_pending"
                    )
                    after["award_review"] = {
                        "status": "pending",
                        "source_url": SCHOLARSHIP_URL,
                        "masters_programme": {
                            "maximum_percentage": "50",
                            "international_applicability": "excluded",
                            "deduction_applied": False,
                        },
                        "alumni_award": {
                            "published_amount": "2000.00",
                            "currency": "GBP",
                            "status": (
                                "international_intake_and_alumni_"
                                "scope_confirmation_required"
                            ),
                        },
                        "entry_award": {
                            "published_amount": "1000.00",
                            "currency": "GBP",
                            "status": (
                                "international_intake_"
                                "confirmation_required"
                            ),
                        },
                        "questions": copy.deepcopy(QUESTIONS),
                        "note": (
                            "No scholarship deduction verified. "
                            "The Master's Scholarship Programme "
                            "excludes international students. "
                            "Other awards require confirmation "
                            "for this international intake."
                        ),
                    }
                    after["deposit_status"] = (
                        "verified_policy_eligibility_required"
                        if course_id in LONDON_IDS
                        else "international_deposit_unconfirmed"
                    )

                changed = before != after
                plans.append((source, after, changed))
                report["courses"].append({
                    "course_id": course_id,
                    "changed": changed,
                    "before": before,
                    "after": after,
                })

            policies = list(
                DepositPolicy.objects.select_for_update().filter(
                    university_id="UK003",
                    academic_year="2027/28",
                    currency="GBP",
                    verified=True,
                )
            )

            if len(policies) != 1:
                raise CommandError(
                    "Expected one existing verified ARU deposit policy."
                )

            policy = policies[0]
            old = copy.deepcopy(policy.rules)

            if (
                not isinstance(old, dict)
                or old.get("schema_version") != 1
                or old.get("kind") != "fixed"
                or old.get("currency") != "GBP"
                or old.get("fee_status") != "INTL"
                or old.get("payment_stage") != "before_cas"
                or not isinstance(old.get("course_ids"), list)
                or not all(
                    isinstance(value, str)
                    for value in old["course_ids"]
                )
            ):
                raise CommandError(
                    "Saved deposit policy structure requires review."
                )

            try:
                amount = Decimal(str(old["amount"]))
                if not amount.is_finite() or amount != Decimal("4000"):
                    raise ValueError
            except Exception as exc:
                raise CommandError(
                    "Expected the existing GBP 4,000 deposit policy."
                ) from exc

            if set(old["course_ids"]) & {PUBLIC_HEALTH, RESEARCH}:
                raise CommandError(
                    "Public Health or research is already in this "
                    "policy scope; review before proceeding."
                )

            new = copy.deepcopy(old)
            new["course_ids"] = sorted(
                set(old["course_ids"]) | LONDON_IDS
            )

            course_kinds = new.get("course_kinds")
            if isinstance(course_kinds, list):
                new["course_kinds"] = sorted(
                    set(course_kinds) | {"postgraduate_taught"}
                )

            evidence_map = new.setdefault(
                "course_source_urls", {}
            )
            if not isinstance(evidence_map, dict):
                raise CommandError(
                    "Saved deposit source map requires review."
                )

            for course_id in sorted(LONDON_IDS):
                source = sources[by_course[course_id].pk]
                evidence_map[course_id] = source.source_url

            policy_changed = old != new
            report["deposit_policy"] = {
                "changed": policy_changed,
                "before": old,
                "after": new,
            }

            report["proposed_items"] = (
                sum(plan[2] for plan in plans)
                + int(policy_changed)
            )

            output = folder / "reconciliation.json"
            output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            if options["apply"]:
                for source, after, changed in plans:
                    if not changed:
                        continue

                    before = next(
                        entry["before"]
                        for entry in report["courses"]
                        if entry["course_id"]
                        == source.tuition.course_id
                    )

                    source.raw_fees = after
                    source.save(update_fields=["raw_fees"])

                    TuitionUpdateAudit.objects.create(
                        tuition=source.tuition,
                        course_identifier=source.tuition.course_id,
                        residency="INTL",
                        before={"raw_fees": before},
                        after={"raw_fees": after},
                    )

                if policy_changed:
                    policy.rules = new
                    policy.save(
                        update_fields=["rules", "updated_at"]
                    )

        report["database_changes"] = bool(
            options["apply"] and report["proposed_items"]
        )
        report["completed"] = True

        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        for entry in report["courses"]:
            state = "update" if entry["changed"] else "unchanged"
            self.stdout.write(
                f"{entry['course_id']}: {state}"
            )

        self.stdout.write(
            "London 50% Master's Scholarship: "
            "excluded for international students."
        )
        self.stdout.write(
            "Other London awards: applicability pending; "
            "eligibility questions saved in review metadata."
        )
        self.stdout.write(
            "Education MPhil: scholarship and deposit remain pending."
        )
        self.stdout.write(
            f"Deposit scope: {len(new['course_ids'])} courses"
        )
        self.stdout.write(
            f"Proposed/updated items: {report['proposed_items']}"
        )
        self.stdout.write(f"Report: {output}")

        if options["apply"]:
            self.stdout.write(
                "Award review metadata and deposit scope saved."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )

        self.stdout.write(
            "Tuition, stored discounts and numeric deposits unchanged."
        )