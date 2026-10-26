import copy
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

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


YEAR = "2026/27"

TARGETS = {
    "C_UK003_12": {
        "title": "LLB (Hons) Law",
        "location": "Cambridge Campus",
        "campus": "Cambridge",
        "slug": "law",
        "code": "M100",
        "amount": "17500.00",
        "award_label": "LLB (Hons)",
    },
    "C_UK003_61": {
        "title": "BSc (Hons) Construction Management",
        "location": "Chelmsford Campus",
        "campus": "Chelmsford",
        "slug": "construction-management",
        "code": "K220",
        "amount": "18400.00",
        "award_label": "BSc (Hons)",
    },
}

FIELDS = [
    "title",
    "level",
    "duration",
    "location",
    "start_month",
    "start_year",
    "intake_months",
    "course_url",
    "ucas_cao_code",
    "work_placement",
]


def money(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid monetary amount.")
    return f"{amount:.2f}"


def validate_policy(rules):
    if (
        not isinstance(rules, dict)
        or rules.get("schema_version") != 1
        or rules.get("kind") != "fixed"
        or rules.get("currency") != "GBP"
        or rules.get("fee_status") != "INTL"
        or rules.get("payment_stage") != "before_cas"
        or rules.get("requires_student_visa") is not True
        or rules.get("full_fee_paying") is not True
        or rules.get("deduct_scholarship_from_deposit") is not False
        or not isinstance(rules.get("course_ids"), list)
        or not all(
            isinstance(value, str)
            for value in rules["course_ids"]
        )
    ):
        raise CommandError(
            "Saved deposit policy requires review."
        )

    try:
        valid_amount = money(rules["amount"]) == "4000.00"
    except (ValueError, TypeError, KeyError):
        valid_amount = False

    if not valid_amount:
        raise CommandError(
            "Expected a GBP 4,000 fixed deposit policy."
        )


class Command(BaseCommand):
    help = (
        "Preview or apply two January 2027 course corrections, "
        "2026/27 fees and course-specific deposit coverage."
    )

    def add_arguments(self, parser):
        parser.add_argument("--evidence", required=True)
        parser.add_argument("--apply", action="store_true")

    def validate_page(self, folder, pages, target):
        url = (
            "https://www.aru.ac.uk/study/undergraduate/"
            + target["slug"]
        )
        matches = [
            page
            for page in pages
            if page.get("source_url", "").rstrip("/") == url
        ]
        if len(matches) != 1:
            raise CommandError(
                f"Expected one saved course page: {url}"
            )

        page = matches[0]
        filename = page.get("html_file")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
        ):
            raise CommandError("Invalid HTML evidence filename.")

        digest = hashlib.sha256(
            (folder / filename).read_bytes()
        ).hexdigest()
        if digest != page.get("sha256"):
            raise CommandError("Saved HTML hash mismatch.")

        instances = []
        for script in page.get("scripts", []):
            parsed = script.get("parsed_json")
            if not isinstance(parsed, dict):
                continue
            values = parsed.get("hasCourseInstance", [])
            if isinstance(values, dict):
                values = [values]
            if isinstance(values, list):
                instances.extend(values)

        confirmed = any(
            isinstance(item, dict)
            and item.get("name") == "Full-time"
            and item.get("courseWorkload") == "P3Y"
            and item.get("startdate") == "2027-01"
            and item.get("duration", "").casefold() == "3 years"
            and any(
                isinstance(location, dict)
                and location.get("name") == target["campus"]
                for location in item.get("location", [])
            )
            for item in instances
        )
        if not confirmed:
            raise CommandError(
                "Standard January 2027 campus route not confirmed."
            )

        text = " ".join(page.get("text", "").split())

        if not re.search(
            rf"UCAS course code\s+{target['code']}\b",
            text,
        ):
            raise CommandError("Expected UCAS code not found.")

        # Isolate the standard honours fee section, stopping
        # before the next foundation or placement route.
        label = re.escape(target["award_label"])
        section = re.search(
            rf"International students\s+{label}.*?"
            rf"{label}\s+(.*?)"
            r"(?=\s+With (?:foundation year|placement)\b)",
            text,
            flags=re.S,
        )
        if section is None:
            raise CommandError(
                "Cannot isolate the standard-route fee section."
            )

        amounts = {
            money(value.replace(",", ""))
            for value in re.findall(
                r"£([\d,]+(?:\.\d{2})?)\s+"
                r"International students starting 2026/27\s+"
                r"\(full-time,\s*per year\)",
                section.group(1),
            )
        }
        if amounts != {target["amount"]}:
            raise CommandError(
                f"Unexpected 2026/27 fee: {sorted(amounts)}"
            )

        # Associate the deposit statement with the 2026/27
        # international fee, rather than a different cohort.
        deposit = re.search(
            r"International students starting 2026/27\s+"
            r"\(full-time,\s*per year\)"
            r"(?:(?!International students starting).)*?"
            r"deposit of £4,000 or a sponsorship letter",
            section.group(1),
            flags=re.S,
        )
        if deposit is None:
            raise CommandError(
                "Deposit statement associated with the "
                "2026/27 fee was not confirmed."
            )

        return page

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        path = Path(options["evidence"])
        if not path.is_absolute():
            path = root / path

        try:
            evidence = json.loads(
                path.read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError) as exc:
            raise CommandError(
                f"Cannot read evidence: {exc}"
            ) from exc

        if (
            not isinstance(evidence, dict)
            or evidence.get("university_id") != "UK003"
            or evidence.get("errors")
            or not isinstance(evidence.get("pages"), list)
        ):
            raise CommandError("Invalid course evidence.")

        try:
            pages = {
                course_id: self.validate_page(
                    path.parent,
                    evidence["pages"],
                    target,
                )
                for course_id, target in TARGETS.items()
            }
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CommandError(
                f"Evidence validation failed: {exc}"
            ) from exc

        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
            "evidence_report": str(path),
            "records": [],
            "deposit_policy": None,
        }

        directory = root / "logs" / "aru"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / (
            "january-2027-routes-"
            + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )

        with transaction.atomic():
            courses = {
                course.course_id: course
                for course in (
                    Course.objects.select_for_update()
                    .filter(
                        university_id="UK003",
                        course_id__in=TARGETS,
                        is_active=True,
                    )
                    .order_by("course_id")
                )
            }
            if set(courses) != set(TARGETS):
                raise CommandError(
                    "Expected both active course records."
                )

            tuitions = list(
                Tuition.objects.select_for_update()
                .filter(course_id__in=TARGETS, residency="INTL")
                .order_by("pk")
            )
            tuition_map = {}
            for tuition in tuitions:
                if tuition.course_id in tuition_map:
                    raise CommandError(
                        "Duplicate international tuition found."
                    )
                tuition_map[tuition.course_id] = tuition

            if (
                set(tuition_map) != set(TARGETS)
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

            # Preserve existing UG questions as review questions,
            # without treating the 2027/28 award as verified for 2026/27.
            questions = {}
            for rule in ScholarshipRule.objects.filter(
                scholarship__university_id="UK003",
                scholarship__level="UG",
                verified=True,
            ).order_by("pk"):
                rules = rule.rules
                if not isinstance(rules, dict):
                    continue

                for question in rules.get(
                    "eligibility_questions", []
                ):
                    if (
                        isinstance(question, dict)
                        and isinstance(question.get("key"), str)
                    ):
                        questions.setdefault(
                            question["key"],
                            copy.deepcopy(question),
                        )

                # Do not override an existing verified 2026/27
                # award covering these records with pending metadata.
                if rules.get("academic_year") == YEAR:
                    conditions = rules.get("conditions", {})
                    scope = (
                        conditions.get("course_id", [])
                        if isinstance(conditions, dict)
                        else []
                    )
                    if isinstance(scope, list) and (
                        set(scope) & set(TARGETS)
                    ):
                        raise CommandError(
                            "A verified 2026/27 scholarship already "
                            "covers this batch. Review it separately."
                        )

            existing_policies = list(
                DepositPolicy.objects.select_for_update().filter(
                    university_id="UK003",
                    academic_year=YEAR,
                    currency="GBP",
                )
            )
            if len(existing_policies) > 1:
                raise CommandError(
                    "Multiple 2026/27 deposit policies found."
                )

            policy = (
                existing_policies[0] if existing_policies else None
            )

            if policy:
                if not policy.verified:
                    raise CommandError(
                        "Existing 2026/27 deposit policy is unverified; "
                        "review before extending it."
                    )
                validate_policy(policy.rules)
                old_policy = copy.deepcopy(policy.rules)
                new_policy = copy.deepcopy(old_policy)
            else:
                # Reuse the question wording only. The amount,
                # academic year and course scope come from the
                # validated 2026/27 course-page evidence.
                template = DepositPolicy.objects.filter(
                    university_id="UK003",
                    academic_year="2027/28",
                    currency="GBP",
                    verified=True,
                ).first()

                if template is None:
                    raise CommandError(
                        "Saved deposit questions/template not found."
                    )
                validate_policy(template.rules)

                old_policy = None
                new_policy = {
                    "schema_version": 1,
                    "academic_year": YEAR,
                    "kind": "fixed",
                    "amount": "4000.00",
                    "currency": "GBP",
                    "fee_status": "INTL",
                    "payment_stage": "before_cas",
                    "requires_student_visa": True,
                    "full_fee_paying": True,
                    "deduct_scholarship_from_deposit": False,
                    "sponsorship_letter_requires_review": True,
                    "offer_letter_confirmation_required": True,
                    "course_ids": [],
                    "course_kinds": [
                        "undergraduate",
                        "undergraduate_taught",
                    ],
                    "source_period": (
                        "course_page_2026_27_fee_section"
                    ),
                    "eligibility_questions": copy.deepcopy(
                        template.rules.get(
                            "eligibility_questions", []
                        )
                    ),
                    "note": (
                        "The reviewed 2026/27 international course "
                        "fee sections publish a GBP 4,000 deposit "
                        "or sponsorship-letter alternative. "
                        "Confirm payment requirements in the offer. "
                        "The deposit is part of tuition, not an "
                        "additional charge."
                    ),
                }

            new_policy["course_ids"] = sorted(
                set(new_policy["course_ids"]) | set(TARGETS)
            )
            new_policy["academic_year"] = YEAR
            source_urls = new_policy.setdefault(
                "course_source_urls", {}
            )
            if not isinstance(source_urls, dict):
                raise CommandError(
                    "Deposit source map requires review."
                )
            for course_id, page in pages.items():
                source_urls[course_id] = page["source_url"]

            policy_changed = old_policy != new_policy
            report["deposit_policy"] = {
                "action": "update" if policy else "create",
                "changed": policy_changed,
                "before": old_policy,
                "after": new_policy,
            }

            for course_id, target in TARGETS.items():
                course = courses[course_id]
                tuition = tuition_map[course_id]
                source = sources.get(tuition.pk)
                page = pages[course_id]

                if (
                    course.title != target["title"]
                    or course.level != "UG"
                    or course.location != target["location"]
                    or course.duration != "3 Years"
                    or course.work_placement
                ):
                    raise CommandError(
                        f"{course_id}: unexpected stored route."
                    )

                before = {
                    "course": {
                        field: getattr(course, field)
                        for field in FIELDS
                    },
                    "tuition": {
                        "tuition_fee": money(tuition.tuition_fee),
                        "currency": tuition.currency,
                        "scholarship": money(tuition.scholarship),
                        "scholarship_source_id": (
                            tuition.scholarship_source_id
                        ),
                    },
                    "source": (
                        {
                            "academic_year": source.academic_year,
                            "source_url": source.source_url,
                            "source_course_code": source.source_course_code,
                            "source_course_name": source.source_course_name,
                            "fee_basis": source.fee_basis,
                            "raw_fees": copy.deepcopy(source.raw_fees),
                        }
                        if source
                        else None
                    ),
                }

                after = copy.deepcopy(before)
                after["course"].update({
                    "start_month": "January",
                    "start_year": 2027,
                    "intake_months": "January",
                    "course_url": page["source_url"],
                    "ucas_cao_code": target["code"],
                })
                after["tuition"].update({
                    "tuition_fee": target["amount"],
                    "scholarship": "0.00",
                    "scholarship_source_id": None,
                })

                raw = {
                    "verified": True,
                    "academic_year": YEAR,
                    "source_fee_category": "International",
                    "currency": "GBP",
                    "annual_tuition": target["amount"],
                    "study_year": 1,
                    "course_kind": "undergraduate",
                    "course_source_url": page["source_url"],
                    "source_sha256": page["sha256"],
                    "campus": target["campus"],
                    "intake": "January 2027",
                    "mapping_kind": "reviewed_intake_correction",
                    "scholarship_status": (
                        "academic_year_applicability_unconfirmed"
                    ),
                    "scholarship_review": {
                        "academic_year": YEAR,
                        "status": "pending",
                        "note": (
                            "The saved 2027/28 scholarship rule "
                            "has not been copied to this January "
                            "2027 / 2026/27 comparison. "
                            "Applicable award terms require confirmation."
                        ),
                        "eligibility_questions": list(
                            questions.values()
                        ),
                    },
                    "deposit_status": (
                        "verified_policy_eligibility_required"
                    ),
                    "published_deposit_evidence": {
                        "academic_year": YEAR,
                        "amount": "4000.00",
                        "currency": "GBP",
                        "alternative": "sponsorship_letter",
                        "source_url": page["source_url"],
                    },
                    "fee_note": (
                        "Published annual international tuition "
                        "for 2026/27, January 2027 standard "
                        "three-year campus route."
                    ),
                    "fee_categories": [{
                        "source_fee_category": "International",
                        "academic_year": YEAR,
                        "currency": "GBP",
                        "annual_tuition": target["amount"],
                        "amount_requires_review": False,
                    }],
                }

                after["source"] = {
                    "academic_year": YEAR,
                    "source_url": page["source_url"],
                    "source_course_code": target["code"],
                    "source_course_name": target["title"],
                    "fee_basis": "annual_tuition",
                    "raw_fees": raw,
                }

                report["records"].append({
                    "course_id": course_id,
                    "changed": before != after,
                    "before": before,
                    "after": after,
                })

            report["proposed_items"] = (
                sum(entry["changed"] for entry in report["records"])
                + int(policy_changed)
            )

            output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            if options["apply"]:
                for entry in report["records"]:
                    if not entry["changed"]:
                        continue

                    course_id = entry["course_id"]
                    course = courses[course_id]
                    tuition = tuition_map[course_id]
                    after = entry["after"]

                    for field, value in after["course"].items():
                        setattr(course, field, value)
                    course.save(
                        update_fields=FIELDS + ["updated_at"]
                    )

                    tuition.tuition_fee = Decimal(
                        after["tuition"]["tuition_fee"]
                    )
                    tuition.scholarship = Decimal("0.00")
                    tuition.scholarship_source_id = None
                    tuition.save(update_fields=[
                        "tuition_fee",
                        "scholarship",
                        "scholarship_source",
                    ])

                    TuitionSource.objects.update_or_create(
                        tuition=tuition,
                        defaults=after["source"],
                    )
                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=course_id,
                        residency="INTL",
                        before=entry["before"],
                        after=after,
                    )

                if policy_changed:
                    if policy:
                        policy.rules = new_policy
                        policy.save(
                            update_fields=["rules", "updated_at"]
                        )
                    else:
                        primary = pages["C_UK003_12"]
                        DepositPolicy.objects.create(
                            university_id="UK003",
                            academic_year=YEAR,
                            currency="GBP",
                            rules=new_policy,
                            source_url=primary["source_url"],
                            source_text=primary["text"],
                            source_sha256=primary["sha256"],
                            verified=True,
                            checked_at=timezone.now(),
                        )

        report["database_changes"] = bool(
            options["apply"] and report["proposed_items"]
        )
        report["completed"] = True
        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        for entry in report["records"]:
            state = "update" if entry["changed"] else "unchanged"
            self.stdout.write(
                f"{entry['course_id']}: GBP "
                f"{entry['before']['tuition']['tuition_fee']} -> "
                f"{entry['after']['tuition']['tuition_fee']}; "
                f"January 2027 / {YEAR} — {state}"
            )

        self.stdout.write(
            f"2026/27 deposit scope: "
            f"{len(new_policy['course_ids'])} courses"
        )
        self.stdout.write(
            "Scholarships: 2026/27 applicability pending; "
            "no deduction verified."
        )
        self.stdout.write(
            f"Proposed/updated items: {report['proposed_items']}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Reviewed fees, intake corrections and deposit scope saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )