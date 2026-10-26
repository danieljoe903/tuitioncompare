import copy
import hashlib
import json
import re
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
    Scholarship,
    ScholarshipRule,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)


YEAR = "2027/28"

UG_URL = (
    "https://www.aru.ac.uk/international/"
    "international-scholarships/"
    "international-undergraduate-scholarship"
)

LONDON_URL = "https://london.aru.ac.uk/admissions/scholarships"

TOPUPS = {
    "C_ARU003_01": {
        "title": "BSc (Hons) International Business (Top-Up)",
        "url": (
            "https://www.aru.ac.uk/study/undergraduate/"
            "international-business-top-up"
        ),
        "amount": "18825.00",
    },
    "C_ARU003_02": {
        "title": "BSc (Hons) International Nursing Studies (Top-Up)",
        "url": (
            "https://www.aru.ac.uk/study/undergraduate/"
            "international-nursing-studies"
        ),
        "amount": "18825.00",
    },
}

LONDON = {
    "C_UK003_19": {
        "title": "MSc Accounting and Financial Management",
        "url": (
            "https://www.aru.ac.uk/study/postgraduate/"
            "accounting-and-financial-management"
        ),
        "amount": "20925.00",
    },
    "C_UK003_24": {
        "title": "LLM International Law",
        "url": (
            "https://www.aru.ac.uk/study/postgraduate/"
            "international-law-london"
        ),
        "amount": "20925.00",
    },
    "C_UK003_29": {
        "title": "MSc International Project Management",
        "url": (
            "https://www.aru.ac.uk/study/postgraduate/"
            "international-project-management"
        ),
        "amount": "20925.00",
    },
    "C_UK003_96": {
        "title": "MSc International Hospitality and Tourism Management",
        "url": (
            "https://www.aru.ac.uk/study/postgraduate/"
            "international-hospitality-and-management"
        ),
        "amount": "20925.00",
    },
}

TARGETS = {**TOPUPS, **LONDON}


def question(key, text, kind="boolean", note=None):
    result = {
        "key": key,
        "type": kind,
        "question": text,
    }

    if note:
        result["note"] = note

    return result


TOPUP_QUESTIONS = [
    question(
        "new_entrant",
        "Are you joining ARU for the first time?",
    ),
    question(
        "full_time",
        "Will you study full-time?",
    ),
    question(
        "entry_year",
        "Which degree year does ARU classify your entry into?",
        "integer",
        "This rule covers entry into Year 2 or Year 3.",
    ),
    question(
        "apcl",
        "Has ARU granted you Accredited Prior Certified Learning?",
        note=(
            "An APCL-adjusted amount requires separate assessment. "
            "Do not answer no simply to obtain the standard award."
        ),
    ),
    question(
        "registration_conditions_met",
        "Have all ARU registration conditions been met?",
    ),
    question(
        "aru_funding_eligibility_confirmed",
        "Is your funding arrangement eligible for this scholarship?",
        note=(
            "Sponsorship and federal tuition loans may affect eligibility."
        ),
    ),
    question(
        "aru_course_award_applicability_confirmed",
        "Has ARU confirmed that this scholarship covers your exact "
        "top-up route and intake?",
        note=(
            "Excluded study modes and work-based routes must not "
            "receive this award automatically."
        ),
    ),
    question(
        "aru_ug_award_confirmed",
        "Has ARU confirmed your undergraduate scholarship?",
        note=(
            "The standard advanced-entry amount modelled here is £1,000. "
            "A different or pro-rata award needs separate calculation."
        ),
    ),
]

LONDON_QUESTIONS = [
    question(
        "offer_received",
        "Have you received an offer for this London course?",
    ),
    question(
        "uk_first_class_degree",
        "Have you achieved a first-class honours degree "
        "from a university in the UK?",
    ),
    question(
        "aru_london_alumni",
        "Did you complete your previous degree at ARU London?",
        note=(
            "This rule uses the narrower published ARU London "
            "alumni definition."
        ),
    ),
    question(
        "aru_london_award_confirmed",
        "Has ARU London confirmed the relevant scholarship "
        "for your course and intake?",
    ),
]


def merge_questions(*groups):
    result = {}

    for group in groups:
        if not isinstance(group, list):
            continue

        for item in group:
            if isinstance(item, dict) and item.get("key"):
                value = copy.deepcopy(item)

                if "question" not in value and value.get("label"):
                    value["question"] = value["label"]

                result.setdefault(value["key"], value)

    return list(result.values())


def make_rule(value, conditions, questions, note):
    return {
        "kind": "fixed",
        "value": value,
        "benefit": "tuition",
        "currency": "GBP",
        "stacking": "exclusive",
        "compatible_ids": [],
        "percentage_basis": "original_tuition",
        "fee_basis_confirmed": True,
        "academic_year": YEAR,
        "study_years": [1],
        "award_status": "conditional",
        "conditions": conditions,
        "scope_note": note,
        "source_period_note": (
            "Current published policy does not explicitly name "
            "an academic year. Associated with the reviewed "
            "2027/28 cohort; individual confirmation is required."
        ),
        "combination_note": (
            "Combination with other scholarships has not been verified. "
            "The calculator selects the best eligible single award."
        ),
        "eligibility_questions": questions,
    }


def specifications():
    advanced = make_rule(
        "1000.00",
        {
            "course_id": sorted(TOPUPS),
            "fee_status": ["INTL"],
            "entry_year": [2, 3],
            "new_entrant": [True],
            "full_time": [True],
            "apcl": [False],
            "registration_conditions_met": [True],
            "aru_funding_eligibility_confirmed": [True],
            "aru_course_award_applicability_confirmed": [True],
            "aru_ug_award_confirmed": [True],
        },
        TOPUP_QUESTIONS,
        (
            "First compared study year of the two reviewed top-up routes. "
            "Only ARU-confirmed Year 2/3 entry with no APCL adjustment "
            "is calculated by this standard £1,000 rule. "
            "Other cases remain subject to individual assessment."
        ),
    )

    entry = make_rule(
        "1000.00",
        {
            "course_id": sorted(LONDON),
            "fee_status": ["INTL"],
            "offer_received": [True],
            "uk_first_class_degree": [True],
            "aru_london_award_confirmed": [True],
        },
        [
            item for item in LONDON_QUESTIONS
            if item["key"] != "aru_london_alumni"
        ],
        (
            "Reviewed London postgraduate courses only. "
            "One £1,000 deduction for the compared first study year. "
            "Requires the published qualification and ARU London "
            "confirmation for the applicant's intake."
        ),
    )

    alumni = make_rule(
        "2000.00",
        {
            "course_id": sorted(LONDON),
            "fee_status": ["INTL"],
            "offer_received": [True],
            "aru_london_alumni": [True],
            "aru_london_award_confirmed": [True],
        },
        [
            item for item in LONDON_QUESTIONS
            if item["key"] != "uk_first_class_degree"
        ],
        (
            "Reviewed London postgraduate courses only. "
            "One £2,000 deduction for eligible ARU London alumni "
            "in the compared first study year. Alumni of another "
            "ARU campus are not assumed eligible under this rule."
        ),
    )

    return [
        {
            "name": (
                "ARU International Undergraduate Scholarship "
                "— Advanced Entry (2027/28)"
            ),
            "level": "UG",
            "amount": "1000.00",
            "url": UG_URL,
            "rule": advanced,
        },
        {
            "name": (
                "ARU London Master's Entry Scholarship (2027/28)"
            ),
            "level": "PG",
            "amount": "1000.00",
            "url": LONDON_URL,
            "rule": entry,
        },
        {
            "name": "ARU London Alumni Scholarship (2027/28)",
            "level": "PG",
            "amount": "2000.00",
            "url": LONDON_URL,
            "rule": alumni,
        },
    ]


class Command(BaseCommand):
    help = (
        "Preview/apply advanced-entry and London scholarship rules "
        "for six reviewed ARU courses."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def fetch_sources(self):
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )

        result = {}

        with requests.Session() as session:
            session.headers["User-Agent"] = (
                "TuitionCompareScholarshipReview/1.0"
            )
            session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )

            for url in [UG_URL, LONDON_URL]:
                self.stdout.write(f"Reading: {url}")

                response = session.get(url, timeout=(30, 60))
                response.raise_for_status()

                if response.url.rstrip("/") != url.rstrip("/"):
                    raise ValueError(
                        f"Unexpected redirect: {response.url}"
                    )

                soup = BeautifulSoup(response.content, "html.parser")

                if soup.find("h1") is None:
                    raise ValueError(f"Missing page heading: {url}")

                main = soup.find("main") or soup

                text = " ".join(
                    main.get_text(" ", strip=True).split()
                )

                result[url] = {
                    "source_url": response.url,
                    "text": text,
                    "html_sha256": hashlib.sha256(
                        response.content
                    ).hexdigest(),
                }

        ug = result[UG_URL]["text"]
        london = result[LONDON_URL]["text"]

        if not re.search(
            r"starting in Year 2 or Year 3.{0,100}£1,000",
            ug,
            re.IGNORECASE,
        ):
            raise ValueError(
                "Cannot verify the £1,000 advanced-entry award."
            )

        if (
            "APCL" not in ug
            or "pro-rata" not in ug
            or "work-based" not in ug
        ):
            raise ValueError(
                "Advanced-entry adjustment/exclusion terms changed."
            )

        if not re.search(
            r"alumni of ARU London.{0,120}£2,000",
            london,
            re.IGNORECASE,
        ):
            raise ValueError(
                "Cannot verify the £2,000 ARU London alumni award."
            )

        if not re.search(
            r"First Class honours degree.{0,200}£1,000",
            london,
            re.IGNORECASE,
        ):
            raise ValueError(
                "Cannot verify the £1,000 London entry award."
            )

        if not re.search(
            r"not available for international students",
            london,
            re.IGNORECASE,
        ):
            raise ValueError(
                "The London Master's Scholarship Programme "
                "exclusion requires review."
            )

        return result

    def build_plan(self, lock=False):
        courses = Course.objects.filter(
            university_id="UK003",
            course_id__in=TARGETS,
        ).order_by("course_id")

        tuitions = Tuition.objects.filter(
            course__university_id="UK003",
            course_id__in=TARGETS,
            residency="INTL",
        ).order_by("course_id")

        source_rows = TuitionSource.objects.filter(
            tuition__course__university_id="UK003",
            tuition__course_id__in=TARGETS,
            tuition__residency="INTL",
        ).order_by("tuition_id")

        if lock:
            courses = courses.select_for_update()
            tuitions = tuitions.select_for_update()
            source_rows = source_rows.select_for_update()

        course_map = {item.course_id: item for item in courses}
        tuition_rows = list(tuitions)
        source_map = {
            item.tuition_id: item for item in source_rows
        }

        if set(course_map) != set(TARGETS):
            raise ValueError("One or more reviewed courses are missing.")

        award_changes = []

        for spec in specifications():
            queryset = Scholarship.objects.filter(
                university_id="UK003",
                name=spec["name"],
            )

            if lock:
                queryset = queryset.select_for_update()

            existing = list(queryset)

            if len(existing) > 1:
                raise ValueError(
                    f"Duplicate scholarship name: {spec['name']}"
                )

            scholarship = existing[0] if existing else None
            rule = None

            if scholarship is not None:
                rule_query = ScholarshipRule.objects.filter(
                    scholarship=scholarship
                )

                if lock:
                    rule_query = rule_query.select_for_update()

                rule = rule_query.first()

                if (
                    rule is not None
                    and rule.source_url
                    and rule.source_url.rstrip("/")
                    != spec["url"].rstrip("/")
                ):
                    raise ValueError(
                        f"Existing rule has a different source: "
                        f"{spec['name']}"
                    )

            defaults = {
                "level": spec["level"],
                "award_type": "Tuition Reduction",
                "award_value": spec["amount"],
                "currency": "GBP",
                "deadline": None,
                "url": spec["url"],
                "eligibility": spec["rule"]["scope_note"],
                "requirements": (
                    "All saved conditions must be satisfied. "
                    "Unknown answers do not establish eligibility."
                ),
                "description": (
                    "Conditional scholarship for the reviewed course "
                    "scope. Gross tuition is retained separately. "
                    "Net tuition is calculated at comparison time."
                ),
            }

            before = {
                "scholarship": (
                    {
                        key: getattr(scholarship, key)
                        for key in defaults
                    }
                    if scholarship is not None
                    else None
                ),
                "rule": (
                    {
                        "verified": rule.verified,
                        "source_url": rule.source_url,
                        "rules": rule.rules,
                    }
                    if rule is not None
                    else None
                ),
            }

            rule_data = copy.deepcopy(spec["rule"])

            if rule is not None and isinstance(rule.rules, dict):
                rule_data["eligibility_questions"] = merge_questions(
                    rule_data["eligibility_questions"],
                    rule.rules.get("eligibility_questions", []),
                )

            after = {
                "scholarship": defaults,
                "rule": {
                    "verified": True,
                    "source_url": spec["url"],
                    "rules": rule_data,
                },
            }

            if before != after:
                award_changes.append({
                    "name": spec["name"],
                    "scholarship_id": (
                        scholarship.pk if scholarship else None
                    ),
                    "before": before,
                    "after": after,
                })

        course_changes = []

        for course_id, target in TARGETS.items():
            course = course_map[course_id]

            if (
                course.title != target["title"]
                or course.course_url.rstrip("/")
                != target["url"].rstrip("/")
                or course.start_year != 2027
            ):
                raise ValueError(
                    f"{course_id}: stored route differs from "
                    "the reviewed course."
                )

            matches = [
                item for item in tuition_rows
                if item.course_id == course_id
            ]

            if len(matches) != 1:
                raise ValueError(
                    f"{course_id}: expected one international tuition."
                )

            tuition = matches[0]
            source = source_map.get(tuition.pk)

            if source is None or not isinstance(source.raw_fees, dict):
                raise ValueError(
                    f"{course_id}: source metadata is missing."
                )

            raw = copy.deepcopy(source.raw_fees)

            if (
                tuition.currency != "GBP"
                or source.academic_year != YEAR
                or raw.get("verified") is not True
                or raw.get("academic_year") != YEAR
                or raw.get("currency") != "GBP"
                or raw.get("source_fee_category") != "International"
                or Decimal(str(tuition.tuition_fee))
                != Decimal(target["amount"])
                or Decimal(str(raw.get("annual_tuition")))
                != Decimal(target["amount"])
            ):
                raise ValueError(
                    f"{course_id}: verified tuition differs "
                    "from the reviewed record."
                )

            old_review = raw.get("scholarship_review")
            if not isinstance(old_review, dict):
                old_review = {}

            old_award_review = raw.get("award_review")
            if not isinstance(old_award_review, dict):
                old_award_review = {}

            topup = course_id in TOPUPS

            review = {
                "status": (
                    "conditional_advanced_entry_rule_requires_confirmation"
                    if topup
                    else "verified_rule_eligibility_required"
                ),
                "academic_year": YEAR,
                "source_url": UG_URL if topup else LONDON_URL,
                "source_period": "current_undated_policy",
                "note": (
                    "Published £1,000 advanced-entry rule saved. "
                    "ARU must confirm exact course coverage and "
                    "entry classification. APCL-adjusted amounts "
                    "are not calculated by this standard rule."
                    if topup
                    else (
                        "Published London entry/alumni rules saved. "
                        "Applicant qualification, alumni status and "
                        "award confirmation determine the deduction. "
                        "Awards are not combined automatically. "
                        "The 50% Master's Scholarship Programme "
                        "is excluded for international students."
                    )
                ),
                "eligibility_questions": merge_questions(
                    TOPUP_QUESTIONS if topup else LONDON_QUESTIONS,
                    old_review.get("eligibility_questions", []),
                    old_award_review.get("eligibility_questions", []),
                    old_award_review.get("questions", []),
                ),
            }

            raw["scholarship_status"] = review["status"]
            raw["scholarship_review"] = review

            if "award_review" in raw:
                raw["award_review"] = copy.deepcopy(review)

            if raw != source.raw_fees:
                course_changes.append({
                    "course_id": course_id,
                    "tuition_id": tuition.pk,
                    "source_id": source.pk,
                    "before": source.raw_fees,
                    "after": raw,
                })

        return {
            "award_changes": award_changes,
            "course_changes": course_changes,
        }

    def save_plan(self, plan):
        for change in plan["award_changes"]:
            scholarship, _ = Scholarship.objects.update_or_create(
                university_id="UK003",
                name=change["name"],
                defaults=change["after"]["scholarship"],
            )

            ScholarshipRule.objects.update_or_create(
                scholarship=scholarship,
                defaults={
                    **change["after"]["rule"],
                    "reviewed_at": timezone.now(),
                },
            )

        for change in plan["course_changes"]:
            source = TuitionSource.objects.get(
                pk=change["source_id"]
            )
            source.raw_fees = change["after"]
            source.save(update_fields=["raw_fees"])

            TuitionUpdateAudit.objects.create(
                tuition_id=change["tuition_id"],
                course_identifier=change["course_id"],
                residency="INTL",
                before={"raw_fees": change["before"]},
                after={"raw_fees": change["after"]},
            )

    def handle(self, *args, **options):
        folder = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aru"
            / (
                "advanced-london-awards-"
                + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            )
        )
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / "reconciliation.json"

        try:
            sources = self.fetch_sources()

            with transaction.atomic():
                if options["apply"]:
                    University.objects.select_for_update().get(
                        pk="UK003"
                    )

                plan = self.build_plan(lock=options["apply"])

                report = {
                    "generated_at": timezone.now().isoformat(),
                    "mode": (
                        "apply_requested"
                        if options["apply"]
                        else "preview"
                    ),
                    "database_changes": False,
                    "university_id": "UK003",
                    "academic_year": YEAR,
                    "sources": sources,
                    "plan": plan,
                }

                output.write_text(
                    json.dumps(
                        report,
                        cls=DjangoJSONEncoder,
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                if options["apply"]:
                    self.save_plan(plan)

        except (
            requests.RequestException,
            OSError,
            ValueError,
            TypeError,
            ArithmeticError,
            University.DoesNotExist,
        ) as exc:
            raise CommandError(
                f"Reconciliation stopped: {exc}. "
                "No database records changed."
            ) from exc

        count = (
            len(plan["award_changes"])
            + len(plan["course_changes"])
        )

        if options["apply"]:
            report["mode"] = "apply"
            report["database_changes"] = count > 0

            output.write_text(
                json.dumps(
                    report,
                    cls=DjangoJSONEncoder,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        self.stdout.write(
            "Advanced-entry £1,000 rule: 2 top-up courses; "
            "course/entry confirmation required; APCL adjustments excluded."
        )
        self.stdout.write(
            "London entry £1,000 rule: 4 courses; conditional."
        )
        self.stdout.write(
            "London alumni £2,000 rule: 4 courses; conditional."
        )
        self.stdout.write(
            "London 50% Master's Scholarship Programme: "
            "excluded for international students."
        )
        self.stdout.write(f"Proposed/updated items: {count}")
        self.stdout.write(f"Report: {output}")

        if options["apply"]:
            self.stdout.write(
                "Scholarship rules and course review metadata saved."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )

        self.stdout.write(
            "Gross tuition, stored scholarship fields and deposit "
            "policies unchanged. Applicable deductions are calculated "
            "from the saved rules when the comparison runs."
        )