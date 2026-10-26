import copy
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from django.db import transaction

from app.models import TuitionUpdateAudit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.models import Course, Tuition, TuitionSource


TARGETS = {
    "C_UK003_05": {
        "old_title": "BSc (Hons) Business and Management",
        "title": "BSc (Hons) Business and Management",
        "level": "UG",
        "old_location": "Cambridge Campus",
        "location": "Peterborough Campus",
        "source_location": "Peterborough",
        "duration": "3 Years",
        "source_duration": "3 years",
        "workload": "P3Y",
        "code": "N127",
        "amount": "18825.00",
        "url": (
            "https://www.aru.ac.uk/study/undergraduate/"
            "business-and-management"
        ),
        "mapping_kind": "campus_correction",
    },
    "C_UK003_30": {
        "old_title": "MSc Foundations in Clinical Psychology",
        "title": "MSc Clinical Applications of Psychology",
        "level": "PG",
        "old_location": "Cambridge Campus",
        "location": "Cambridge Campus",
        "source_location": "Cambridge",
        "duration": "1 Year",
        "source_duration": "1 year full-time",
        "workload": "P1Y",
        "code": "",
        "amount": "20925.00",
        "url": (
            "https://www.aru.ac.uk/study/postgraduate/"
            "clinical-applications-of-psychology"
        ),
        "mapping_kind": "explicit_replacement",
    },
}

COURSE_FIELDS = [
    "title",
    "level",
    "location",
    "duration",
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


class Command(BaseCommand):
    help = (
        "Preview the Business campus correction and explicit "
        "Psychology MSc replacement. No database writes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--evidence",
            required=True,
            help="Path to the three-course evidence report.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the validated course and tuition updates.",
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
            or evidence.get("errors")
        ):
            raise CommandError(
                "Invalid evidence or collection errors present."
            )

        report = {
            "generated_at": timezone.now().isoformat(),
            "university_id": "UK003",
            "mode": "preview",
            "database_changes": False,
            "evidence_report": str(path),
            "records": [],
            "blocked": [],
            "held": [{
                "course_id": "C_UK003_31",
                "reason": (
                    "Standard-route intake differs between "
                    "the course overview and structured data."
                ),
            }],
        }

        for course_id, target in TARGETS.items():
            try:
                report["records"].append(
                    self.plan_record(
                        path.parent,
                        evidence["pages"],
                        course_id,
                        target,
                    )
                )
            except (
                CommandError,
                OSError,
                ValueError,
                TypeError,
                KeyError,
            ) as exc:
                report["blocked"].append({
                    "course_id": course_id,
                    "reason": str(exc),
                })

        report["proposed_changes"] = sum(
            entry["changed"] for entry in report["records"]
        )

        folder = root / "logs" / "aru"
        folder.mkdir(parents=True, exist_ok=True)

        output = folder / (
            "business-psychology-preview-"
            + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )

        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if options["apply"] and not report["blocked"]:
            self.apply_records(
                report,
                path.parent,
                evidence["pages"],
            )

            report["mode"] = "apply"
            report["database_changes"] = any(
                entry["changed"]
                for entry in report["records"]
            )

            output.write_text(
                json.dumps(
                    report,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        for entry in report["records"]:
            state = "update" if entry["changed"] else "unchanged"
            self.stdout.write(
                f"{entry['course_id']}: GBP "
                f"{entry['before']['tuition']['tuition_fee']} -> "
                f"{entry['after']['tuition']['tuition_fee']}; "
                f"September 2027 — {state}"
            )
            self.stdout.write(
                f"  Mapping: {entry['mapping_kind']}"
            )

            for field, value in entry["after"]["course"].items():
                previous = entry["before"]["course"][field]
                if previous != value:
                    self.stdout.write(
                        f"  {field}: {previous} -> {value}"
                    )

        for entry in report["blocked"]:
            self.stderr.write(
                f"{entry['course_id']}: {entry['reason']}"
            )

        self.stdout.write(
            f"\nMatched records: {len(report['records'])}"
        )
        self.stdout.write(
            f"Proposed changes: {report['proposed_changes']}"
        )
        self.stdout.write(
            f"Blocked: {len(report['blocked'])}"
        )
        self.stdout.write(f"Report: {output}")
        if options["apply"] and not report["blocked"]:
            self.stdout.write(
                "Reviewed course corrections, tuition and "
                "source metadata saved."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )
        self.stdout.write(
            "Scholarship applicability and deposit scope "
            "require separate reconciliation."
        )
        self.stdout.write(
            "C_UK003_31 remains pending intake confirmation."
        )

        if report["blocked"]:
            raise CommandError(
                "Some proposals require review. "
                "See the saved report."
            )

    def apply_records(self, report, folder, pages):
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
                    "Expected both active reviewed courses. "
                    "Run the preview again."
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

            if (
                set(tuition_by_course) != set(TARGETS)
                or any(
                    tuition.currency != "GBP"
                    for tuition in tuitions
                )
            ):
                raise CommandError(
                    "Each course requires one GBP "
                    "international tuition record."
                )

            list(
                TuitionSource.objects.select_for_update()
                .filter(tuition__in=tuitions)
                .order_by("pk")
            )

            # Validate every proposal again while the affected
            # records are locked. No writes occur until both pass.
            for entry in report["records"]:
                course_id = entry["course_id"]

                current = self.plan_record(
                    folder,
                    pages,
                    course_id,
                    TARGETS[course_id],
                )

                if (
                    current["before"] != entry["before"]
                    or current["after"] != entry["after"]
                ):
                    raise CommandError(
                        f"{course_id}: evidence or database "
                        "values changed during the run. "
                        "Run the preview again."
                    )

            for entry in report["records"]:
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
                tuition.save(update_fields=["tuition_fee"])

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
    def validated_page(self, folder, pages, target):
        matches = [
            page
            for page in pages
            if page.get("source_url", "").rstrip("/")
            == target["url"]
        ]

        if len(matches) != 1:
            raise CommandError(
                "Expected one collected course page."
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
            isinstance(instance, dict)
            and instance.get("name") == "Full-time"
            and instance.get("courseMode") == "Onsite"
            and instance.get("courseWorkload")
            == target["workload"]
            and instance.get("startdate") == "2027-09"
            and instance.get("duration", "").casefold()
            == target["source_duration"].casefold()
            and any(
                isinstance(location, dict)
                and location.get("name")
                == target["source_location"]
                for location in instance.get("location", [])
            )
            for instance in instances
        )

        if not confirmed:
            raise CommandError(
                "Standard September 2027 campus route "
                "was not confirmed."
            )

        text = " ".join(page.get("text", "").split())

        if target["code"] and not re.search(
            rf"UCAS course code\s+{re.escape(target['code'])}\b",
            text,
        ):
            raise CommandError(
                "Expected UCAS course code not found."
            )

        if target["level"] == "UG":
            # Select the honours fee section before the
            # separately listed foundation-year route.
            section = re.search(
                r"International students\s+BSc \(Hons\).*?"
                r"BSc \(Hons\)\s+(.*?)"
                r"(?=\s+With foundation year\b)",
                text,
                flags=re.S,
            )
        else:
            section = re.search(
                r"International students\s+MSc\s+"
                r"Part-time option\(s\)\s+MSc\s+(.*?)"
                r"(?=\s+Part-time option\(s\)(?=\s|$))",
                text,
                flags=re.S,
            )

        if section is None:
            raise CommandError(
                "Cannot isolate the standard full-time fee section."
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
                f"Unexpected international annual fee: "
                f"{sorted(amounts)}"
            )

        return page

    def plan_record(self, folder, pages, course_id, target):
        page = self.validated_page(folder, pages, target)

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
            or course.location
            not in {target["old_location"], target["location"]}
            or course.work_placement
        ):
            raise CommandError(
                "Stored title, campus, award or route "
                "differs from the reviewed candidate."
            )

        # Identify possible collisions before proposing a
        # correction/replacement. Do not create a duplicate.
        collisions = list(
            Course.objects.filter(
                university_id="UK003",
                is_active=True,
                title=target["title"],
                level=target["level"],
                duration=target["duration"],
                location=target["location"],
                start_year=2027,
                start_month="September",
            )
            .exclude(course_id=course_id)
            .values_list("course_id", flat=True)
        )

        if collisions:
            raise CommandError(
                "Possible duplicate route already exists: "
                + ", ".join(collisions)
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

        before = {
            "course": {
                field: getattr(course, field)
                for field in COURSE_FIELDS
            },
            "tuition": {
                "tuition_fee": money(tuition.tuition_fee),
                "currency": tuition.currency,
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
            "location": target["location"],
            "start_month": "September",
            "start_year": 2027,
            "intake_months": "September",
            "course_url": target["url"],
            "ucas_cao_code": target["code"],
        })
        after["tuition"]["tuition_fee"] = target["amount"]

        raw = (
            copy.deepcopy(source.raw_fees)
            if source and isinstance(source.raw_fees, dict)
            else {}
        )

        raw.update({
            "verified": True,
            "academic_year": "2027/28",
            "source_fee_category": "International",
            "currency": "GBP",
            "annual_tuition": target["amount"],
            "study_year": 1,
            "course_kind": (
                "undergraduate"
                if target["level"] == "UG"
                else "postgraduate_taught"
            ),
            "course_source_url": target["url"],
            "source_sha256": page["sha256"],
            "campus": target["source_location"],
            "intake": "September 2027",
            "mapping_kind": target["mapping_kind"],
            "fee_method": "reviewed_named_standard_route",
            "scholarship_status": "applicability_unconfirmed",
            "deposit_status": "scope_reconciliation_pending",
            "fee_note": (
                "Published annual international tuition for "
                "September 2027, standard full-time campus "
                "route. Foundation, placement and part-time "
                "routes are excluded."
            ),
            "fee_categories": [{
                "source_fee_category": "International",
                "academic_year": "2027/28",
                "currency": "GBP",
                "annual_tuition": target["amount"],
                "amount_requires_review": False,
            }],
        })
        raw.pop("verification_reason", None)

        if target["mapping_kind"] == "explicit_replacement":
            raw.setdefault("route_replacement", {
                "previous_title": target["old_title"],
                "replacement_title": target["title"],
                "source_url": target["url"],
                "note": (
                    "Explicit replacement with a currently "
                    "advertised course. This record does not "
                    "claim a university-confirmed renaming."
                ),
            })
        else:
            raw.setdefault("campus_correction", {
                "previous_location": target["old_location"],
                "corrected_location": target["location"],
                "source_url": target["url"],
                "note": (
                    "Matched the exact advertised Business "
                    "and Management title and UCAS code N127 "
                    "to Peterborough."
                ),
            })

        after["source"] = {
            "academic_year": "2027/28",
            "source_url": target["url"],
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