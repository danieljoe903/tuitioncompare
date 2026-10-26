import copy
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.models import (
    Course,
    DepositPolicy,
    ScholarshipRule,
    Tuition,
    TuitionSource,
)
from django.db import transaction

from app.models import TuitionUpdateAudit


YEAR = "2027/28"

TARGETS = {
    "C_UK003_95": {
        "old_title": "BSc (Hons) Banking and Finance",
        "title": "BSc (Hons) Banking, Finance and Fintech",
        "level": "UG",
        "duration": "3 Years",
        "campus": "Chelmsford",
        "slug": "undergraduate/banking-finance-and-fintech",
        "amount": "18825.00",
        "code": "N309",
        "mapping_kind": "explicit_replacement",
    },
    "C_UK003_99": {
        "old_title": "MA Education with Early Childhood",
        "title": "MA Education with Early Childhood",
        "level": "PG",
        "duration": "1 Year",
        "campus": "Chelmsford",
        "slug": "postgraduate/education",
        "amount": "18825.00",
        "code": "",
        "mapping_kind": "named_pathway",
    },
    "C_UK003_100": {
        "old_title": "MA Education with Leadership and Management",
        "title": "MA Education with Leadership and Management",
        "level": "PG",
        "duration": "1 Year",
        "campus": "Chelmsford",
        "slug": "postgraduate/education",
        "amount": "18825.00",
        "code": "",
        "mapping_kind": "named_pathway",
    },
}

RULE_SCOPES = {
    (
        "ARU International Undergraduate Scholarship "
        "— Lower — Year 1 (2027/28)"
    ): {"C_UK003_95"},
    (
        "ARU International Undergraduate Scholarship "
        "— Higher — Year 1 (2027/28)"
    ): {"C_UK003_95"},
    (
        "ARU International Postgraduate Scholarship "
        "— ARU graduate eligibility (2027/28)"
    ): {"C_UK003_99", "C_UK003_100"},
}


def money(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid amount.")
    return f"{amount:.2f}"


def read_config(page):
    configs = []

    for script in page.get("scripts", []):
        text = script.get("text", "")

        for match in re.finditer(
            r"\b(?:var\s+)?configOptions\s*=\s*",
            text,
        ):
            try:
                value, _ = json.JSONDecoder().raw_decode(
                    text[match.end():].lstrip()
                )
            except ValueError:
                continue

            if isinstance(value, dict):
                configs.append(value)

    if len(configs) != 1:
        raise CommandError(
            "Expected one embedded course configuration."
        )

    return configs[0]


class Command(BaseCommand):
    help = (
        "Preview three reviewed ARU route updates, scholarship "
        "scopes and deposit scope. No database writes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--evidence",
            required=True,
            help="Path to the collected campus-intake evidence.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the validated preview in one transaction.",
        )

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
            or not isinstance(evidence.get("pages"), list)
        ):
            raise CommandError("Invalid ARU evidence report.")

        # These known issues do not mean that the Education
        # course page failed to download.
        allowed_errors = {
            (
                course_id,
                "No application link found; "
                "manual route review required.",
            )
            for course_id in ["C_UK003_99", "C_UK003_100"]
        }

        for error in evidence.get("errors", []):
            if (
                error.get("course_id"),
                error.get("error"),
            ) not in allowed_errors:
                raise CommandError(
                    "Unexpected collection error: "
                    + json.dumps(error, ensure_ascii=False)
                )

        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "preview",
            "database_changes": False,
            "evidence_report": str(path),
            "courses": [],
            "scholarship_rules": [],
            "deposit_policy": None,
            "blocked": [],
        }

        for course_id, target in TARGETS.items():
            try:
                plan = self.plan_course(
                    path.parent,
                    evidence["pages"],
                    course_id,
                    target,
                )
                report["courses"].append(plan)
            except (
                CommandError,
                ValueError,
                InvalidOperation,
                TypeError,
                KeyError,
                OSError,
            ) as exc:
                report["blocked"].append({
                    "course_id": course_id,
                    "reason": str(exc),
                })

        # Do not propose scope extensions for a blocked batch.
        if not report["blocked"]:
            self.plan_scholarships(report)
            self.plan_deposit(report)

        report["proposed_course_changes"] = sum(
            entry["changed"] for entry in report["courses"]
        )
        report["ready_for_apply_implementation"] = (
            not report["blocked"]
        )
        report["mode"] = (
            "apply" if options["apply"] else "preview"
        )
        folder = root / "logs" / "aru"
        folder.mkdir(parents=True, exist_ok=True)

        output = folder / (
            "three-route-preview-"
            + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )
        output.write_text(
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        if options["apply"] and not report["blocked"]:
            self.apply_plan(
                report,
                path.parent,
                evidence["pages"],
            )
            report["database_changes"] = any(
                entry["changed"]
                for entry in report["courses"]
            ) or any(
                entry["changed"]
                for entry in report["scholarship_rules"]
            ) or bool(
                report["deposit_policy"]
                and report["deposit_policy"]["changed"]
            )

            output.write_text(
                json.dumps(
                    report,
                    indent=2,
                    ensure_ascii=False,
                ),
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

            if (
                entry["before"]["course"]["title"]
                != entry["after"]["course"]["title"]
            ):
                self.stdout.write(
                    "  Replacement: "
                    f"{entry['before']['course']['title']} -> "
                    f"{entry['after']['course']['title']}"
                )

        for entry in report["scholarship_rules"]:
            self.stdout.write(
                f"{entry['name']}: "
                f"{len(entry['after']['conditions']['course_id'])} "
                "courses in proposed scope"
            )

        if report["deposit_policy"]:
            self.stdout.write(
                "Proposed deposit scope: "
                f"{len(report['deposit_policy']['after']['course_ids'])} "
                "courses"
            )

        for entry in report["blocked"]:
            self.stderr.write(
                f"{entry['course_id']}: {entry['reason']}"
            )

        self.stdout.write(f"Report: {output}")
        if options["apply"] and not report["blocked"]:
            self.stdout.write(
                "Validated tuition, source metadata, scholarship "
                "scopes and deposit scope saved."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )
        
        self.stdout.write(
            "Scholarship questions and existing conditions "
            "are preserved. Deductions remain conditional."
        )

        if report["blocked"]:
            raise CommandError(
                "Preview contains blocked items. "
                "See the saved report."
            )

    def get_page(self, folder, pages, url):
        matches = [
            page
            for page in pages
            if page.get("source_url", "").rstrip("/") == url
        ]

        if not matches:
            raise CommandError(
                f"Collected source page not found: {url}"
            )

        # Validate every capture against its own saved HTML.
        for page in matches:
            filename = page.get("html_file")

            if (
                not isinstance(filename, str)
                or Path(filename).name != filename
            ):
                raise CommandError(
                    "Invalid saved HTML filename."
                )

            try:
                content = (folder / filename).read_bytes()
            except OSError as exc:
                raise CommandError(
                    f"Cannot read saved HTML: {filename}"
                ) from exc

            digest = hashlib.sha256(content).hexdigest()

            if digest != page.get("sha256"):
                raise CommandError(
                    f"Saved HTML hash mismatch: {filename}"
                )

        if len(matches) > 1:
            reference_text = " ".join(
                matches[0].get("text", "").split()
            )
            reference_config = read_config(matches[0])

            for page in matches[1:]:
                current_text = " ".join(
                    page.get("text", "").split()
                )
                current_config = read_config(page)

                if (
                    current_text != reference_text
                    or current_config != reference_config
                ):
                    raise CommandError(
                        "Duplicate captures contain different "
                        "course text or configuration; "
                        "manual review is required."
                    )

        return matches[0]
    
    def plan_course(self, folder, pages, course_id, target):
        url = "https://www.aru.ac.uk/study/" + target["slug"]
        page = self.get_page(folder, pages, url)

        course = Course.objects.filter(
            university_id="UK003",
            course_id=course_id,
            is_active=True,
        ).first()

        if course is None:
            raise CommandError("Active database course not found.")

        if (
            course.title
            not in {target["old_title"], target["title"]}
            or course.level != target["level"]
            or course.duration != target["duration"]
            or course.location != target["campus"] + " Campus"
            or course.work_placement
        ):
            raise CommandError(
                "Stored title, level, campus or route "
                "differs from the reviewed mapping."
            )

        tuitions = list(
            Tuition.objects.filter(
                course=course,
                residency="INTL",
            )
        )
        if len(tuitions) != 1 or tuitions[0].currency != "GBP":
            raise CommandError(
                "One GBP international tuition is required."
            )

        tuition = tuitions[0]
        source = TuitionSource.objects.filter(
            tuition=tuition
        ).first()

        if target["level"] == "UG":
            self.validate_banking(page, target)
        else:
            self.validate_education(page, target)

        fields = [
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

        before = {
            "course": {
                field: getattr(course, field)
                for field in fields
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
                    "raw_fees": source.raw_fees,
                }
                if source
                else None
            ),
        }

        after = copy.deepcopy(before)
        after["course"].update({
            "title": target["title"],
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
            "course_kind": (
                "undergraduate"
                if target["level"] == "UG"
                else "postgraduate_taught"
            ),
            "course_source_url": url,
            "source_sha256": page["sha256"],
            "mapping_kind": target["mapping_kind"],
            "campus": target["campus"],
            "intake": "September 2027",
            "fee_method": "reviewed_named_route",
            "scholarship_status": (
                "verified_rule_eligibility_required"
            ),
            "deposit_status": (
                "verified_policy_eligibility_required"
            ),
            "fee_note": (
                "Published annual international tuition for "
                "September 2027, full-time campus study. "
                "Distance learning, APCL, foundation and "
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

        if target["mapping_kind"] == "explicit_replacement":
            # Keep a stable replacement record across reruns.
            if "route_replacement" not in raw:
                raw["route_replacement"] = {
                    "previous_title": target["old_title"],
                    "replacement_title": target["title"],
                    "reason": (
                        "Explicit replacement with the currently "
                        "advertised Banking, Finance and Fintech "
                        "standard honours route."
                    ),
                    "source_url": url,
                }
        else:
            raw["named_pathway"] = target["title"]
            raw["parent_course"] = "MA Education"

        after["source"] = {
            "academic_year": YEAR,
            "source_url": url,
            "source_course_code": target["code"],
            "source_course_name": target["title"],
            "fee_basis": "annual_tuition",
            "raw_fees": raw,
        }

        return {
            "course_id": course_id,
            "mapping_kind": target["mapping_kind"],
            "changed": before != after,
            "before": before,
            "after": after,
        }

    def validate_banking(self, page, target):
        instances = []

        for script in page.get("scripts", []):
            parsed = script.get("parsed_json")
            if isinstance(parsed, dict):
                values = parsed.get("hasCourseInstance", [])
                if isinstance(values, dict):
                    values = [values]
                if isinstance(values, list):
                    instances.extend(values)

        found = any(
            isinstance(item, dict)
            and item.get("name") == "Full-time"
            and item.get("courseWorkload") == "P3Y"
            and item.get("startdate") == "2027-09"
            and item.get("duration", "").casefold() == "3 years"
            and any(
                location.get("name") == target["campus"]
                for location in item.get("location", [])
                if isinstance(location, dict)
            )
            for item in instances
        )

        if not found:
            raise CommandError(
                "September 2027 standard Banking route "
                "not confirmed."
            )

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
                "Standard Banking fee section not found."
            )

        amounts = {
            money(value.replace(",", ""))
            for value in re.findall(
                r"£([\d,]+(?:\.\d{2})?)\s+"
                r"International students starting 2027/28\s+"
                r"\(full-time,\s*per year\)",
                section.group(1),
            )
        }

        if amounts != {target["amount"]}:
            raise CommandError(
                f"Unexpected Banking fee: {sorted(amounts)}"
            )

        if not re.search(
            r"UCAS course code\s+N309\b",
            text,
        ):
            raise CommandError("Banking UCAS code not confirmed.")

    def validate_education(self, page, target):
        text = page.get("text", "")
        pathway = (
            target["title"]
            + " (available in "
        )

        if pathway not in text:
            raise CommandError(
                "Named Education pathway not found."
            )

        start = text.index(pathway)
        availability = text[start:].split(")", 1)[0]

        if target["campus"] not in availability:
            raise CommandError(
                "Chelmsford pathway availability not confirmed."
            )

        config = read_config(page)
        options = [
            option
            for option in config.get("CourseOptions", [])
            if (
                option.get("Duration", {}).get("Name")
                == "1 year full-time"
                and any(
                    date.get("Name") == "September 2027"
                    for date in option.get("StartDate", [])
                )
                and any(
                    location.get("ShortName") == target["campus"]
                    for location in option.get("Location", [])
                )
            )
        ]

        if len(options) != 1:
            raise CommandError(
                "Expected one full-time September 2027 "
                "campus Education option."
            )

        amounts = set()
        for fee in options[0].get("TuitionFees", []):
            band = fee.get("FeeBand") or {}
            if band.get("Description") == (
                "International students starting 2027/28 "
                "(full-time, per year)"
            ):
                amounts.add(money(band["Value"]))

        if amounts != {target["amount"]}:
            raise CommandError(
                f"Unexpected Education campus fee: "
                f"{sorted(amounts)}"
            )

    def plan_scholarships(self, report):
        for name, additions in RULE_SCOPES.items():
            rules = list(
                ScholarshipRule.objects.filter(
                    scholarship__university_id="UK003",
                    scholarship__name=name,
                    verified=True,
                )
            )

            if len(rules) != 1:
                report["blocked"].append({
                    "course_id": "scholarship_scope",
                    "reason": (
                        f"Expected one verified rule: {name}"
                    ),
                })
                continue

            rule = rules[0]
            old = copy.deepcopy(rule.rules)

            if (
                not isinstance(old, dict)
                or old.get("academic_year") != YEAR
                or not isinstance(old.get("conditions"), dict)
                or not isinstance(
                    old["conditions"].get("course_id"), list
                )
                or not all(
                    isinstance(value, str)
                    for value in old["conditions"]["course_id"]
                )
            ):
                report["blocked"].append({
                    "course_id": "scholarship_scope",
                    "reason": f"Unsupported saved rule: {name}",
                })
                continue

            expected_kind = (
                "fixed_maximum"
                if "Postgraduate" in name
                else "fixed"
            )

            if (
                old.get("kind") != expected_kind
                or old.get("study_years") != [1]
                or (
                    "Postgraduate" in name
                    and old["conditions"].get("aru_graduate")
                    != [True]
                )
            ):
                report["blocked"].append({
                    "course_id": "scholarship_scope",
                    "reason": (
                        f"Saved eligibility structure "
                        f"requires review: {name}"
                    ),
                })
                continue

            new = copy.deepcopy(old)
            new["conditions"]["course_id"] = sorted(
                set(old["conditions"]["course_id"]) | additions
            )

            report["scholarship_rules"].append({
                "rule_id": rule.pk,
                "name": name,
                "changed": old != new,
                "before": old,
                "after": new,
            })

    def apply_plan(self, report, evidence_folder, pages):
        with transaction.atomic():
            # Lock the courses before rechecking the proposed plan.
            courses = {
                course.course_id: course
                for course in (
                    Course.objects.select_for_update()
                    .filter(
                        university_id="UK003",
                        course_id__in=TARGETS,
                    )
                    .order_by("course_id")
                )
            }

            if set(courses) != set(TARGETS):
                raise CommandError(
                    "Course records changed after preview."
                )

            tuitions = list(
                Tuition.objects.select_for_update()
                .filter(
                    course_id__in=TARGETS,
                    residency="INTL",
                )
                .order_by("pk")
            )

            tuition_by_course = {}
            for tuition in tuitions:
                if tuition.course_id in tuition_by_course:
                    raise CommandError(
                        "Duplicate international tuition found."
                    )
                tuition_by_course[tuition.course_id] = tuition

            if set(tuition_by_course) != set(TARGETS):
                raise CommandError(
                    "International tuition records changed."
                )

            list(
                TuitionSource.objects.select_for_update()
                .filter(tuition__in=tuitions)
                .order_by("pk")
            )

            # Revalidate evidence and current database values while
            # the affected records are locked.
            for entry in report["courses"]:
                course_id = entry["course_id"]
                current = self.plan_course(
                    evidence_folder,
                    pages,
                    course_id,
                    TARGETS[course_id],
                )

                if (
                    current["before"] != entry["before"]
                    or current["after"] != entry["after"]
                ):
                    raise CommandError(
                        f"{course_id}: values changed during "
                        "the run. Run the preview again."
                    )

            locked_rules = {}
            for entry in report["scholarship_rules"]:
                rule = (
                    ScholarshipRule.objects.select_for_update()
                    .get(pk=entry["rule_id"])
                )

                if not rule.verified or rule.rules != entry["before"]:
                    raise CommandError(
                        "Scholarship rules changed during the run."
                    )

                locked_rules[rule.pk] = rule

            deposit_entry = report["deposit_policy"]
            if deposit_entry is None:
                raise CommandError(
                    "Validated deposit policy is required."
                )

            policy = (
                DepositPolicy.objects.select_for_update()
                .get(pk=deposit_entry["policy_id"])
            )

            if (
                not policy.verified
                or policy.university_id != "UK003"
                or policy.academic_year != YEAR
                or policy.currency != "GBP"
                or policy.rules != deposit_entry["before"]
            ):
                raise CommandError(
                    "Deposit policy changed during the run."
                )

            for entry in report["courses"]:
                if not entry["changed"]:
                    continue

                course_id = entry["course_id"]
                course = courses[course_id]
                tuition = tuition_by_course[course_id]
                after = entry["after"]

                for field, value in after["course"].items():
                    setattr(course, field, value)

                course.save(
                    update_fields=(
                        list(after["course"]) + ["updated_at"]
                    )
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

            for entry in report["scholarship_rules"]:
                if entry["changed"]:
                    rule = locked_rules[entry["rule_id"]]
                    rule.rules = entry["after"]
                    rule.save(update_fields=["rules"])

            if deposit_entry["changed"]:
                policy.rules = deposit_entry["after"]
                policy.save(
                    update_fields=["rules", "updated_at"]
                )

                
    def plan_deposit(self, report):
        policies = list(
            DepositPolicy.objects.filter(
                university_id="UK003",
                academic_year=YEAR,
                currency="GBP",
                verified=True,
            )
        )

        if len(policies) != 1:
            report["blocked"].append({
                "course_id": "deposit_policy",
                "reason": (
                    "Expected one verified GBP deposit policy."
                ),
            })
            return

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
            report["blocked"].append({
                "course_id": "deposit_policy",
                "reason": "Unsupported saved deposit policy.",
            })
            return

        try:
            valid_amount = money(old["amount"]) == "4000.00"
        except (KeyError, ValueError, InvalidOperation, TypeError):
            valid_amount = False

        if not valid_amount:
            report["blocked"].append({
                "course_id": "deposit_policy",
                "reason": "Expected the saved GBP 4,000 amount.",
            })
            return

        new = copy.deepcopy(old)
        new["course_ids"] = sorted(
            set(old["course_ids"]) | set(TARGETS)
        )

        report["deposit_policy"] = {
            "policy_id": policy.pk,
            "changed": old != new,
            "before": old,
            "after": new,
        }