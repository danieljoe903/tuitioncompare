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


YEAR = "2027/28"

TARGETS = {
    "C_UK003_01": {
        "title": "BSc (Hons) Accounting and Finance",
        "campus": "Chelmsford",
        "code": "N421",
        "slug": "accounting-and-finance",
        "amount": "18825.00",
    },
    "C_UK003_36": {
        "title": "BSc (Hons) Quantity Surveying",
        "campus": "Chelmsford",
        "code": "K240",
        "slug": "quantity-surveying",
        "amount": "19750.00",
    },
}

RULE_NAMES = [
    (
        "ARU International Undergraduate Scholarship "
        "— Lower — Year 1 (2027/28)"
    ),
    (
        "ARU International Undergraduate Scholarship "
        "— Higher — Year 1 (2027/28)"
    ),
]

COURSE_FIELDS = [
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


def snapshot(course, tuition, source):
    return {
        "course": {
            field: getattr(course, field)
            for field in COURSE_FIELDS
        },
        "tuition": {
            "tuition_fee": str(tuition.tuition_fee),
            "currency": tuition.currency,
            "scholarship": str(tuition.scholarship),
            "scholarship_source_id": tuition.scholarship_source_id,
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
            if source is not None
            else None
        ),
    }


class Command(BaseCommand):
    help = (
        "Preview or import two reviewed ARU September 2027 "
        "campus routes and extend existing award/deposit scopes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--evidence",
            help="Optional path to campus-intake-evidence.json.",
        )

    def read_evidence(self, supplied_path):
        root = Path(settings.BASE_DIR)

        if supplied_path:
            path = Path(supplied_path)
            if not path.is_absolute():
                path = root / path
        else:
            files = sorted(
                (root / "logs" / "aru").glob(
                    "campus-intakes-*/campus-intake-evidence.json"
                )
            )
            if not files:
                raise CommandError(
                    "No campus-intake-evidence.json found."
                )
            path = files[-1]

        try:
            data = json.loads(
                path.read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError) as exc:
            raise CommandError(
                f"Cannot read evidence: {exc}"
            ) from exc

        if (
            not isinstance(data, dict)
            or data.get("university_id") != "UK003"
            or data.get("errors")
            or not isinstance(data.get("pages"), list)
        ):
            raise CommandError(
                "Evidence is invalid or contains collection errors."
            )

        return path, data

    def validate_page(self, folder, pages, target):
        url = (
            "https://www.aru.ac.uk/study/undergraduate/"
            + target["slug"]
        )

        matches = [
            page
            for page in pages
            if isinstance(page, dict)
            and page.get("source_url", "").rstrip("/")
            == url
        ]

        if len(matches) != 1:
            raise CommandError(
                f"Expected one collected source page: {url}"
            )

        page = matches[0]
        filename = page.get("html_file")

        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
        ):
            raise CommandError("Invalid evidence HTML filename.")

        try:
            content = (folder / filename).read_bytes()
        except OSError as exc:
            raise CommandError(
                f"Missing saved HTML evidence: {filename}"
            ) from exc

        digest = hashlib.sha256(content).hexdigest()

        if digest != page.get("sha256"):
            raise CommandError(
                f"Saved HTML hash does not match: {filename}"
            )

        # Confirm the campus and date within the same standard
        # three-year, full-time course instance.
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

        matched_instance = any(
            isinstance(instance, dict)
            and instance.get("name") == "Full-time"
            and instance.get("courseWorkload") == "P3Y"
            and instance.get("startdate") == "2027-09"
            and instance.get("duration", "").casefold()
            in {"3 years", "3 years full-time"}
            and any(
                isinstance(location, dict)
                and location.get("name") == target["campus"]
                for location in instance.get("location", [])
            )
            for instance in instances
        )

        if not matched_instance:
            raise CommandError(
                f"Standard September 2027 campus route "
                f"was not confirmed: {url}"
            )

        # Select the standard honours fee section, stopping
        # before the separately published placement route.
        text = page.get("text", "")
        section = re.search(
            r"International students\s+BSc \(Hons\).*?"
            r"BSc \(Hons\)\s+(.*?)"
            r"(?=\s+With placement\b)",
            text,
            flags=re.S,
        )

        if section is None:
            raise CommandError(
                f"Cannot isolate the standard BSc fee section: {url}"
            )

        amounts = {
            Decimal(value.replace(",", ""))
            for value in re.findall(
                r"£([\d,]+(?:\.\d{2})?)\s+"
                r"International students starting 2027/28\s+"
                r"\(full-time,\s*per year\)",
                section.group(1),
            )
        }

        if amounts != {Decimal(target["amount"])}:
            raise CommandError(
                f"Unexpected standard-route fee for {url}: "
                f"{sorted(str(value) for value in amounts)}"
            )

        if not re.search(
            rf"UCAS course code\s+{re.escape(target['code'])}\b",
            text,
        ):
            raise CommandError(
                f"UCAS code not confirmed: {target['code']}"
            )

        return url, digest

    def handle(self, *args, **options):
        evidence_path, evidence = self.read_evidence(
            options["evidence"]
        )

        verified_pages = {
            course_id: self.validate_page(
                evidence_path.parent,
                evidence["pages"],
                target,
            )
            for course_id, target in TARGETS.items()
        }

        now = timezone.now()
        directory = Path(settings.BASE_DIR) / "logs" / "aru"
        directory.mkdir(parents=True, exist_ok=True)

        output = directory / (
            "confirmed-campus-import-"
            + now.strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )

        report = {
            "generated_at": now.isoformat(),
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
            "evidence_report": str(evidence_path),
            "courses": [],
            "scholarship_rules": [],
            "deposit_policy": None,
        }

        # Validate and plan everything before making changes.
        with transaction.atomic():
            plans = []

            for course_id, target in TARGETS.items():
                course = (
                    Course.objects.select_for_update()
                    .filter(
                        course_id=course_id,
                        university_id="UK003",
                        is_active=True,
                    )
                    .first()
                )

                if course is None:
                    raise CommandError(
                        f"{course_id}: active course not found."
                    )

                if (
                    course.title != target["title"]
                    or course.level != "UG"
                    or course.location
                    != target["campus"] + " Campus"
                    or course.duration != "3 Years"
                    or course.work_placement
                ):
                    raise CommandError(
                        f"{course_id}: stored route differs from "
                        "the reviewed standard campus route."
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
                        f"{course_id}: one GBP international "
                        "tuition record is required."
                    )

                tuition = tuitions[0]
                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                before = snapshot(course, tuition, source)
                after = copy.deepcopy(before)
                url, digest = verified_pages[course_id]

                after["course"].update({
                    "start_month": "September",
                    "start_year": 2027,
                    "intake_months": "September",
                    "course_url": url,
                    "ucas_cao_code": target["code"],
                })

                after["tuition"].update({
                    "tuition_fee": target["amount"],
                    "scholarship": "0.00",
                    "scholarship_source_id": None,
                })

                raw = (
                    copy.deepcopy(source.raw_fees)
                    if source and isinstance(source.raw_fees, dict)
                    else {}
                )

                raw.update({
                    "verified": True,
                    "academic_year": YEAR,
                    "source_fee_category": "International",
                    "currency": "GBP",
                    "annual_tuition": target["amount"],
                    "study_year": 1,
                    "course_kind": "undergraduate",
                    "fee_method": "reviewed_standard_campus_route",
                    "course_source_url": url,
                    "campus": target["campus"],
                    "intake": "September 2027",
                    "route": "standard_three_year_honours",
                    "source_sha256": digest,
                    "scholarship_status": (
                        "verified_rule_eligibility_required"
                    ),
                    "deposit_status": (
                        "verified_policy_eligibility_required"
                    ),
                    "fee_note": (
                        "Published annual international tuition "
                        "for the standard three-year honours "
                        "route, September 2027. Foundation and "
                        "placement routes are excluded."
                    ),
                    "fee_categories": [{
                        "source_fee_category": "International",
                        "academic_year": YEAR,
                        "currency": "GBP",
                        "annual_tuition": target["amount"],
                        "amount_requires_review": False,
                    }],
                })
                raw.pop("verification_reason", None)

                after["source"] = {
                    "academic_year": YEAR,
                    "source_url": url,
                    "source_course_code": target["code"],
                    "source_course_name": target["title"],
                    "fee_basis": "annual_tuition",
                    "raw_fees": raw,
                }

                # Compare amounts numerically so Decimal display
                # differences do not create repeated updates.
                comparable_before = copy.deepcopy(before)
                for field in ("tuition_fee", "scholarship"):
                    comparable_before["tuition"][field] = (
                        f"{Decimal(before['tuition'][field]):.2f}"
                    )

                changed = comparable_before != after

                plans.append(
                    (course, tuition, after, changed)
                )
                report["courses"].append({
                    "course_id": course_id,
                    "changed": changed,
                    "before": before,
                    "after": after,
                })

            rule_plans = []

            for name in RULE_NAMES:
                rules = list(
                    ScholarshipRule.objects.select_for_update()
                    .filter(
                        scholarship__university_id="UK003",
                        scholarship__name=name,
                        verified=True,
                    )
                )

                if len(rules) != 1:
                    raise CommandError(
                        f"Expected one verified rule: {name}"
                    )

                rule = rules[0]
                old = copy.deepcopy(rule.rules)

                if (
                    not isinstance(old, dict)
                    or old.get("academic_year") != YEAR
                    or old.get("kind") != "fixed"
                    or old.get("study_years") != [1]
                    or not isinstance(old.get("conditions"), dict)
                ):
                    raise CommandError(
                        f"Unsupported saved rule: {name}"
                    )

                existing_ids = old["conditions"].get("course_id")
                if (
                    not isinstance(existing_ids, list)
                    or not all(
                        isinstance(value, str)
                        for value in existing_ids
                    )
                ):
                    raise CommandError(
                        f"Invalid course scope: {name}"
                    )

                new = copy.deepcopy(old)
                new["conditions"]["course_id"] = sorted(
                    set(existing_ids) | set(TARGETS)
                )

                rule_plans.append((rule, new, new != old))
                report["scholarship_rules"].append({
                    "name": name,
                    "changed": new != old,
                    "before": old,
                    "after": new,
                })

            policies = list(
                DepositPolicy.objects.select_for_update().filter(
                    university_id="UK003",
                    academic_year=YEAR,
                    currency="GBP",
                    verified=True,
                )
            )

            if len(policies) != 1:
                raise CommandError(
                    "Expected one verified ARU GBP deposit policy."
                )

            policy = policies[0]
            old_policy = copy.deepcopy(policy.rules)

            if (
                not isinstance(old_policy, dict)
                or old_policy.get("schema_version") != 1
                or old_policy.get("kind") != "fixed"
                or old_policy.get("currency") != "GBP"
                or old_policy.get("fee_status") != "INTL"
                or old_policy.get("payment_stage") != "before_cas"
                or not isinstance(
                    old_policy.get("course_ids"), list
                )
                or not all(
                    isinstance(value, str)
                    for value in old_policy["course_ids"]
                )
            ):
                raise CommandError(
                    "Saved ARU deposit policy requires review."
                )

            try:
                deposit_amount = Decimal(
                    str(old_policy["amount"])
                )
                if (
                    not deposit_amount.is_finite()
                    or deposit_amount != Decimal("4000.00")
                ):
                    raise ValueError
            except Exception as exc:
                raise CommandError(
                    "Expected the existing GBP 4,000 policy."
                ) from exc

            new_policy = copy.deepcopy(old_policy)
            new_policy["course_ids"] = sorted(
                set(old_policy["course_ids"]) | set(TARGETS)
            )
            policy_changed = new_policy != old_policy

            report["deposit_policy"] = {
                "changed": policy_changed,
                "before": old_policy,
                "after": new_policy,
            }

            proposed = (
                sum(plan[3] for plan in plans)
                + sum(plan[2] for plan in rule_plans)
                + int(policy_changed)
            )
            report["proposed_items"] = proposed

            # Save the reviewable plan before any database writes.
            output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            if options["apply"]:
                for course, tuition, after, changed in plans:
                    if not changed:
                        continue

                    for field, value in after["course"].items():
                        setattr(course, field, value)

                    course.save(
                        update_fields=COURSE_FIELDS + ["updated_at"]
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

                    entry = next(
                        item
                        for item in report["courses"]
                        if item["course_id"] == course.course_id
                    )
                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=course.course_id,
                        residency="INTL",
                        before=entry["before"],
                        after=entry["after"],
                    )

                for rule, new, changed in rule_plans:
                    if changed:
                        rule.rules = new
                        rule.save(update_fields=["rules"])

                if policy_changed:
                    policy.rules = new_policy
                    policy.save(
                        update_fields=["rules", "updated_at"]
                    )

        # The transaction has committed successfully here.
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
                f"{entry['course_id']}: GBP "
                f"{entry['before']['tuition']['tuition_fee']} -> "
                f"{entry['after']['tuition']['tuition_fee']}; "
                f"September 2027 — {state}"
            )

        for entry in report["scholarship_rules"]:
            state = "update" if entry["changed"] else "unchanged"
            count = len(
                entry["after"]["conditions"]["course_id"]
            )
            self.stdout.write(
                f"{entry['name']}: {count} courses — {state}"
            )

        self.stdout.write(
            f"Deposit scope: "
            f"{len(new_policy['course_ids'])} courses"
        )
        self.stdout.write(
            f"Proposed/updated items: {report['proposed_items']}"
        )
        self.stdout.write(f"Report: {output}")

        if options["apply"]:
            self.stdout.write(
                "Reviewed tuition, source metadata and applicable "
                "scholarship/deposit scopes saved."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )

        self.stdout.write(
            "Eligibility questions preserved. Scholarship "
            "deductions remain conditional. Numeric stored "
            "deposits unchanged."
        )
        self.stdout.write(
            "Law and Construction Management remain pending."
        )