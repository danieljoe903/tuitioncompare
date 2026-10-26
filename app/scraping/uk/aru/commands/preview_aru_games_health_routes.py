import json
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from app.models import (
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
)

from app.scraping.uk.aru.commands.preview_aru_specialist_routes import Command as EvidenceValidator


TARGETS = {
    "C_UK003_65": {
        "slug": "undergraduate/computer-games-programming",
        "old_title": "BSc (Hons) Computer Games Technology",
        "schema_name": "Computer Games Programming",
        "schema_award": "BSc (Hons)",
        "schema_start": "2027-09",
        "schema_workload": "P3Y",
        "schema_campus": "Cambridge",
        "route_markers": [
            "Computer Games Programming",
            "September 2027",
            "Cambridge",
            "I611",
            "3 years",
        ],
        "changes": {
            "title": "BSc (Hons) Computer Games Programming",
            "level": "UG",
            "location": "Cambridge Campus",
            "duration": "3 Years",
            "start_month": "September",
            "start_year": 2027,
            "ucas_cao_code": "I611",
        },
        "academic_year": "2027/28",
        "expected_fee": "18825.00",
        "kind": "undergraduate_taught",
        "fee_note": (
            "Annual international tuition for the standard "
            "three-year Cambridge BSc route, September 2027. "
            "Optional foundation and placement routes are excluded."
        ),
    },
    "C_ARU003_RES_01": {
        "slug": "postgraduate/school-of-allied-health",
        "old_title": "MSc by Research (Medicine & Health)",
        "schema_name": "Allied Health and Social Care",
        "schema_award": "MPhil, PhD",
        "schema_start": "2027-01",
        "schema_campus": "Cambridge",
        "route_markers": [
            "Allied Health and Social Care",
            "MPhil",
            "January 2027",
            "Cambridge",
            "2026/27",
        ],
        "changes": {
            "title": "MPhil Allied Health and Social Care",
            "level": "PG",
            "location": "Cambridge Campus",
            "duration": "1 to 3 Years",
            "start_month": "January",
            "start_year": 2027,
            "ucas_cao_code": "",
        },
        "academic_year": "2026/27",
        "expected_fee": "17500.00",
        "kind": "postgraduate_research",
        "fee_note": (
            "Annual full-time international research tuition for "
            "January 2027 / 2026/27. MPhil completion is published "
            "as 1–3 years. This is not a total programme price. "
            "Additional project-specific bench fees may apply "
            "and have not been included."
        ),
    },
    "C_UK003_94": {
        "slug": "undergraduate/3d-animation",
        "old_title": "BA (Hons) Computer Animation and Visual Effects",
        "schema_name": "3D Animation",
        "schema_award": "BA (Hons)",
        "schema_start": "2027-09",
        "schema_workload": "P3Y",
        "schema_campus": "Cambridge",
        "route_markers": [
            "3D Animation",
            "September 2027",
            "Cambridge",
            "I626",
            "3 years",
        ],
        "changes": {
            "title": "BA (Hons) 3D Animation",
            "level": "UG",
            "location": "Cambridge Campus",
            "duration": "3 Years",
            "start_month": "September",
            "start_year": 2027,
            "ucas_cao_code": "I626",
        },
        "academic_year": None,
        "expected_fee": None,
        "kind": "undergraduate_taught",
        "fee_note": (
            "The September 2027 BA 3D Animation route is identified, "
            "but an applicable international tuition amount has "
            "not been verified. UK tuition must not be substituted. "
            "Retain an unavailable-fee message."
        ),
    },
}


def amount_string(value):
    if value is None:
        return None

    amount = Decimal(str(value).replace(",", ""))

    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid monetary amount.")

    return format(amount.quantize(Decimal("0.01")), ".2f")


class Command(BaseCommand):
    help = (
        "Preview or apply reviewed ARU Games Programming, Allied "
        "Health MPhil and 3D Animation replacements."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--evidence",
            help="Path to the saved campus-intake evidence JSON.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Save the reviewed replacements and tuition evidence.",
        )
        parser.add_argument(
            "--accept-replacements",
            action="store_true",
            help="Explicitly accept the course replacements.",
        )

    @staticmethod
    def read_report(path):
        return json.loads(path.read_text(encoding="utf-8"))

    def find_evidence(self, supplied_path):
        if supplied_path:
            path = Path(supplied_path)

            if not path.is_absolute():
                path = Path(settings.BASE_DIR) / path

            if not path.is_file():
                raise CommandError(
                    f"Evidence report not found: {path}"
                )

            return path.resolve()

        folder = Path(settings.BASE_DIR) / "logs" / "aru"
        candidates = []

        for path in folder.glob(
            "campus-intakes-*/campus-intake-evidence.json"
        ):
            try:
                report = self.read_report(path)

                ids = {
                    entry["database_course"]["course_id"]
                    for entry in report.get("courses", [])
                }

                if (
                    report.get("university_id") == "UK003"
                    and set(TARGETS).issubset(ids)
                ):
                    candidates.append(path)

            except (OSError, ValueError, KeyError, TypeError):
                continue

        if not candidates:
            raise CommandError(
                "No matching evidence report found. "
                "Supply --evidence explicitly."
            )

        return max(
            candidates,
            key=lambda path: path.parent.name,
        ).resolve()

    def validate_schema(self, page, target):
        schemas = []

        def collect(value):
            if isinstance(value, list):
                for item in value:
                    collect(item)

            elif isinstance(value, dict):
                schemas.append(value)

                if "@graph" in value:
                    collect(value["@graph"])

        for script in page.get("scripts", []):
            collect(script.get("parsed_json"))

        matching_courses = [
            item
            for item in schemas
            if item.get("@type") == "Course"
            and item.get("name") == target["schema_name"]
            and item.get("award") == target["schema_award"]
        ]

        if len(matching_courses) != 1:
            raise ValueError(
                "Cannot isolate the expected course name and award "
                "in the saved structured data."
            )

        instances = matching_courses[0].get(
            "hasCourseInstance", []
        )

        if isinstance(instances, dict):
            instances = [instances]

        matches = []

        for instance in instances:
            if not isinstance(instance, dict):
                continue

            start = (
                instance.get("startdate")
                or instance.get("startDate")
                or ""
            )

            locations = instance.get("location", [])

            if isinstance(locations, dict):
                locations = [locations]

            campus_matches = any(
                isinstance(location, dict)
                and location.get("name") == target["schema_campus"]
                for location in locations
            )

            if (
                not str(start).startswith(target["schema_start"])
                or instance.get("courseMode") != "Onsite"
                or not campus_matches
            ):
                continue

            if target["kind"] == "postgraduate_research":
                if "full-time" not in str(
                    instance.get("name", "")
                ).casefold():
                    continue

            elif (
                instance.get("name") != "Full-time"
                or instance.get("courseWorkload")
                != target["schema_workload"]
            ):
                continue

            matches.append(instance)

        if len(matches) != 1:
            raise ValueError(
                "Cannot isolate the reviewed intake, campus "
                "and standard study route."
            )

        if target["kind"] == "postgraduate_research":
            if not re.search(
                r"MPhil:\s*full-time\s*1\s*[-–]\s*3\s*years",
                page["text"],
                re.IGNORECASE,
            ):
                raise ValueError(
                    "Cannot verify the MPhil full-time duration."
                )

    def extract_fee(self, course_id, page, target):
        text = " ".join(page["text"].split())

        if course_id == "C_UK003_65":
            pattern = (
                r"International students\s+"
                r"BSc \(Hons\)\s+With placement\s+"
                r"With foundation year\s+BSc \(Hons\)\s+"
                r"£\s*([\d,]+(?:\.\d{2})?)\s+"
                r"International students starting\s+"
                r"(\d{4}/\d{2})\s+"
                r"\(full-time,\s*per year\)"
            )

            matches = re.findall(
                pattern,
                text,
                re.IGNORECASE,
            )

            if len(matches) != 1:
                raise ValueError(
                    "Cannot isolate the standard Games Programming fee."
                )

            amount, year = matches[0]

        elif course_id == "C_ARU003_RES_01":
            pattern = (
                r"International students,\s*"
                r"(\d{4}/\d{2})\s*"
                r"\(per year\)\s*"
                r"£\s*([\d,]+(?:\.\d{2})?)"
            )

            matches = re.findall(
                pattern,
                text,
                re.IGNORECASE,
            )

            if len(matches) != 1:
                raise ValueError(
                    "Cannot isolate the full-time international "
                    "research fee."
                )

            year, amount = matches[0]

       
        else:
            # Inspect the fee section only. Navigation and general
            # scholarship guidance do not verify international tuition.
            fee_section = re.search(
                r"\bFees and funding\b(.*?)(?:"
                r"\bPlacement year fee\b|"
                r"\bLiving costs\b|"
                r"\bBursaries, scholarships and part-time work\b|"
                r"\Z)",
                text,
                re.IGNORECASE | re.DOTALL,
            )

            if fee_section is None:
                raise ValueError(
                    "Cannot isolate the Animation fee section."
                )

            fee_text = fee_section.group(1)

            if re.search(
                r"\bInternational students\b",
                fee_text,
                re.IGNORECASE,
            ):
                raise ValueError(
                    "The Animation fee section contains international "
                    "fee information. Review it before assigning a price."
                )

            if not re.search(
                r"£\s*[\d,]+(?:\.\d{2})?\s+"
                r"UK students starting\s+\d{4}/\d{2}",
                fee_text,
                re.IGNORECASE,
            ):
                raise ValueError(
                    "Cannot confirm the captured Animation "
                    "UK-only fee presentation."
                )

            # International tuition remains unavailable.
            # Never substitute the published UK amount.
            return None, None

        amount = amount_string(amount)

        if (
            amount != target["expected_fee"]
            or year != target["academic_year"]
        ):
            raise ValueError(
                f"Unexpected fee/period: GBP {amount}, {year}."
            )

        return amount, year

    def snapshot(self, course, tuition, source, fields):
        return {
            "course": {
                field: getattr(course, field)
                for field in fields
            },
            "tuition": {
                "id": tuition.pk,
                "residency": tuition.residency,
                "currency": tuition.currency,
                "tuition_fee": amount_string(
                    tuition.tuition_fee
                ),
            },
            "source": (
                {
                    "academic_year": source.academic_year,
                    "source_url": source.source_url,
                    "source_course_name": source.source_course_name,
                    "source_course_code": source.source_course_code,
                    "fee_basis": source.fee_basis,
                    "raw_fees": deepcopy(source.raw_fees),
                }
                if source is not None
                else None
            ),
        }

    def load_records(self, course_id, lock=False):
        courses = Course.objects
        tuitions = Tuition.objects
        sources = TuitionSource.objects

        if lock:
            courses = courses.select_for_update()
            tuitions = tuitions.select_for_update()
            sources = sources.select_for_update()

        course = courses.get(
            university_id="UK003",
            course_id=course_id,
        )

        tuition_rows = list(
            tuitions.filter(
                course=course,
                residency="INTL",
            )
        )

        if (
            len(tuition_rows) != 1
            or tuition_rows[0].currency != "GBP"
        ):
            raise ValueError(
                "Exactly one GBP international tuition is required."
            )

        tuition = tuition_rows[0]
        source = sources.filter(tuition=tuition).first()

        return course, tuition, source

    def make_proposal(self, report, evidence_path, course_id):
        target = TARGETS[course_id]
        validator = EvidenceValidator()

        # Retains the existing saved-HTML checksum, URL and
        # course-entry checks.
        page, _, source_url = validator.load_page(
            report,
            evidence_path,
            course_id,
            target,
        )

        self.validate_schema(page, target)
        amount, year = self.extract_fee(
            course_id,
            page,
            target,
        )

        course, tuition, source = self.load_records(course_id)

        if course.title not in {
            target["old_title"],
            target["changes"]["title"],
        }:
            raise ValueError(
                f"Unexpected stored title: {course.title!r}"
            )

        duplicate_ids = list(
            Course.objects.filter(
                university_id="UK003",
                course_url__in=[
                    source_url.rstrip("/"),
                    source_url.rstrip("/") + "/",
                ],
            )
            .exclude(course_id=course_id)
            .values_list("course_id", flat=True)
        )

        if duplicate_ids:
            raise ValueError(
                "Another record already uses this route: "
                + ", ".join(duplicate_ids)
            )

        after_course = {
            **target["changes"],
            "course_url": source_url,
        }

        before = self.snapshot(
            course,
            tuition,
            source,
            list(after_course),
        )

        changes = {
            field: {
                "before": before["course"][field],
                "after": value,
            }
            for field, value in after_course.items()
            if before["course"][field] != value
        }

        return {
            "course_id": course_id,
            "mapping": "explicit_replacement_proposal",
            "replacement_accepted": False,
            "before": before,
            "after_course": after_course,
            "course_changes": changes,
            "tuition": {
                "stored_amount": amount_string(tuition.tuition_fee),
                "proposed_amount": amount,
                "currency": "GBP",
                "academic_year": year,
                "fee_basis": (
                    "annual_tuition"
                    if amount is not None
                    else "unavailable"
                ),
                "source_fee_verified": amount is not None,
                "note": target["fee_note"],
            },
            "source": {
                "source_url": source_url,
                "source_sha256": page["sha256"],
                "source_course_name": target["schema_name"],
                "source_course_code": (
                    target["changes"]["ucas_cao_code"]
                ),
                "course_kind": target["kind"],
                "evidence_generated_at": report.get("generated_at"),
            },
        }

    def save_proposal(self, proposal, records):
        course, tuition, source = records

        course_id = proposal["course_id"]
        after_course = proposal["after_course"]
        fee = proposal["tuition"]
        evidence = proposal["source"]

        before = self.snapshot(
            course,
            tuition,
            source,
            list(after_course),
        )

        if before != proposal["before"]:
            raise CommandError(
                f"{course_id}: database records changed after preview. "
                "Run the command again."
            )

        changed_fields = [
            field
            for field, value in after_course.items()
            if getattr(course, field) != value
        ]

        for field in changed_fields:
            setattr(course, field, after_course[field])

        if changed_fields:
            update_fields = list(changed_fields)

            if any(
                field.name == "updated_at"
                for field in Course._meta.fields
            ):
                update_fields.append("updated_at")

            course.save(update_fields=update_fields)

        if (
            source is not None
            and not isinstance(source.raw_fees, dict)
        ):
            raise CommandError(
                f"{course_id}: existing source metadata requires review."
            )

        raw = deepcopy(source.raw_fees) if source else {}
        academic_year = fee["academic_year"] or ""
        verified = fee["source_fee_verified"]

        source_changed = (
            source is None
            or source.source_url != evidence["source_url"]
            or source.academic_year != academic_year
        )

        if changed_fields:
            history = raw.get("route_review_history", [])

            if not isinstance(history, list):
                raise CommandError(
                    f"{course_id}: route history requires review."
                )

            # Keep the previous evidence without repeatedly nesting
            # the entire accumulated route history.
            previous_source = deepcopy(before["source"])

            if previous_source:
                previous_source["raw_fees"].pop(
                    "route_review_history",
                    None,
                )

            history.append({
                "recorded_at": timezone.now().isoformat(),
                "mapping": "explicit_replacement",
                "replacement_accepted": True,
                "before_course": before["course"],
                "after_course": deepcopy(after_course),
                "previous_tuition_source": previous_source,
                "evidence_generated_at": evidence[
                    "evidence_generated_at"
                ],
            })

            raw["route_review_history"] = history

        raw.update({
            "verified": verified,
            "academic_year": academic_year,
            "currency": "GBP",
            "source_fee_category": "International",
            "course_kind": evidence["course_kind"],
            "course_source_url": evidence["source_url"],
            "source_sha256": evidence["source_sha256"],
            "source_course_name": evidence["source_course_name"],
            "source_course_code": evidence["source_course_code"],
            "fee_basis": fee["fee_basis"],
            "annual_tuition": fee["proposed_amount"],
            "fee_note": fee["note"],
            "mapping": "explicit_replacement",
            "replacement_accepted": True,
        })

        if verified:
            amount = fee["proposed_amount"]

            raw["fee_categories"] = [{
                "source_fee_category": "International",
                "academic_year": academic_year,
                "currency": "GBP",
                "annual_tuition": amount,
                "raw_annual_fee": amount,
                "annotation": fee["note"],
                "footnote_marker": "",
                "amount_requires_review": False,
            }]

            raw.pop("verification_reason", None)

            if amount_string(tuition.tuition_fee) != amount:
                tuition.tuition_fee = Decimal(amount)
                tuition.save(update_fields=["tuition_fee"])

        else:
            # The old numeric fee remains historical. The saved
            # metadata marks it unavailable for the replacement.
            raw["fee_categories"] = []
            raw["verification_reason"] = fee["note"]

        # Preserve later scholarship/deposit reconciliation when
        # this importer is repeated for the same route and year.
        if changed_fields or source_changed:
            raw["scholarship_status"] = "applicability_pending"
            raw["deposit_status"] = "applicability_pending"

        defaults = {
            "academic_year": academic_year,
            "source_url": evidence["source_url"],
            "source_course_name": evidence["source_course_name"],
            "source_course_code": evidence["source_course_code"],
            "fee_basis": fee["fee_basis"],
            "raw_fees": raw,
        }

        if source is None or any(
            getattr(source, field) != value
            for field, value in defaults.items()
        ):
            source, _ = TuitionSource.objects.update_or_create(
                tuition=tuition,
                defaults=defaults,
            )

        after = self.snapshot(
            course,
            tuition,
            source,
            list(after_course),
        )

        changed = before != after

        if changed:
            TuitionUpdateAudit.objects.create(
                tuition=tuition,
                course_identifier=course_id,
                residency="INTL",
                before=before,
                after=after,
            )

        return {
            "course_id": course_id,
            "changed": changed,
            "replacement_accepted": True,
            "fee_verified": verified,
            "before": before,
            "after": after,
        }

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
        if (
            options["apply"]
            and not options["accept_replacements"]
        ):
            raise CommandError(
                "Use both --apply and --accept-replacements "
                "to save these replacements."
            )

        evidence_path = self.find_evidence(options["evidence"])

        try:
            report = self.read_report(evidence_path)
        except (OSError, ValueError) as exc:
            raise CommandError(
                f"Cannot read evidence: {exc}"
            ) from exc

        if report.get("university_id") != "UK003":
            raise CommandError("Evidence is not for ARU UK003.")

        proposals = []
        blocked = []

        for course_id in TARGETS:
            try:
                proposal = self.make_proposal(
                    report,
                    evidence_path,
                    course_id,
                )

                proposals.append(proposal)
                fee = proposal["tuition"]

                if fee["source_fee_verified"]:
                    self.stdout.write(
                        f"{course_id}: "
                        f"GBP {fee['stored_amount']} -> "
                        f"{fee['proposed_amount']}; "
                        f"{fee['academic_year']} — "
                        "verified-fee replacement proposal"
                    )
                else:
                    self.stdout.write(
                        f"{course_id}: route-only replacement; "
                        "international fee unavailable"
                    )

                for field, change in proposal[
                    "course_changes"
                ].items():
                    self.stdout.write(
                        f"  {field}: {change['before']} -> "
                        f"{change['after']}"
                    )

                self.stdout.write(
                    f"  Fee note: {fee['note']}"
                )

            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                ArithmeticError,
                CommandError,
                Course.DoesNotExist,
                Course.MultipleObjectsReturned,
            ) as exc:
                blocked.append({
                    "course_id": course_id,
                    "reason": str(exc),
                })

                self.stderr.write(
                    f"{course_id}: BLOCKED — {exc}"
                )

        folder = Path(settings.BASE_DIR) / "logs" / "aru"
        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        preview_path = folder / f"games-health-preview-{stamp}.json"

        preview = {
            "generated_at": timezone.now().isoformat(),
            "university_id": "UK003",
            "mode": "preview",
            "database_changes": False,
            "evidence_report": str(evidence_path),
            "collection_issues": report.get("errors", []),
            "proposals": proposals,
            "blocked": blocked,
            "excluded_course_ids": ["C_UK003_26"],
            "excluded_note": (
                "London MPH remains outside this batch."
            ),
        }

        try:
            self.write_report(preview_path, preview)
        except OSError as exc:
            raise CommandError(
                f"Cannot save preview report: {exc}. "
                "No database records changed."
            ) from exc

        verified_count = sum(
            proposal["tuition"]["source_fee_verified"]
            for proposal in proposals
        )

        self.stdout.write(
            f"\nVerified-fee proposals: {verified_count}"
        )
        self.stdout.write(
            f"Route-only proposals: "
            f"{len(proposals) - verified_count}"
        )
        self.stdout.write(f"Blocked: {len(blocked)}")
        self.stdout.write(f"Preview report: {preview_path}")

        if blocked:
            raise CommandError(
                "Some proposals require review. "
                "No database records changed."
            )

        if not options["apply"]:
            self.stdout.write(
                "Preview only. No database records changed."
            )
            self.stdout.write(
                "To save, run with "
                "--apply --accept-replacements."
            )
            return

        if {p["course_id"] for p in proposals} != set(TARGETS):
            raise CommandError(
                "The complete three-course preview is required. "
                "No database records changed."
            )

        ordered = sorted(
            proposals,
            key=lambda proposal: proposal["course_id"],
        )

        with transaction.atomic():
            locked_records = {}

            # Lock every target before changing any target.
            for proposal in ordered:
                course_id = proposal["course_id"]

                locked_records[course_id] = self.load_records(
                    course_id,
                    lock=True,
                )

            # Recheck evidence and database state after locking.
            for proposal in ordered:
                refreshed = self.make_proposal(
                    report,
                    evidence_path,
                    proposal["course_id"],
                )

                if refreshed != proposal:
                    raise CommandError(
                        f"{proposal['course_id']}: evidence or database "
                        "records changed after preview. "
                        "No changes from this batch were committed."
                    )

            results = [
                self.save_proposal(
                    proposal,
                    locked_records[proposal["course_id"]],
                )
                for proposal in ordered
            ]

        changed_count = sum(
            result["changed"]
            for result in results
        )

        applied_path = folder / (
            "games-health-applied-"
            + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )

        applied_report = {
            "generated_at": timezone.now().isoformat(),
            "university_id": "UK003",
            "mode": "apply",
            "database_changes": changed_count > 0,
            "changed_records": changed_count,
            "evidence_report": str(evidence_path),
            "preview_report": str(preview_path),
            "results": results,
            "scholarship_scopes_changed": False,
            "deposit_policies_changed": False,
        }

        try:
            self.write_report(applied_path, applied_report)
        except OSError as exc:
            raise CommandError(
                "Database changes were committed and database audit "
                "records saved, but the applied JSON report could "
                f"not be written: {exc}"
            ) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Applied changed records: {changed_count}"
            )
        )
        self.stdout.write(f"Applied report: {applied_path}")
        self.stdout.write(
            "Accepted replacements and tuition evidence saved. "
            "3D Animation international tuition remains unavailable."
        )
        self.stdout.write(
            "Scholarship rules, stored discounts and deposit "
            "policies unchanged. Applicability requires reconciliation."
        )