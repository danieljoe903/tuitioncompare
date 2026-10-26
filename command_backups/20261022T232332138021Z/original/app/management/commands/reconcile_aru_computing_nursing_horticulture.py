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
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
)


TARGETS = {
    "C_ARU003_RES_02": {
        "old_title": "MSc by Research (Computing & Information Science)",
        "title": "MPhil Computing and Information Science",
        "location": "Cambridge Campus",
        "slug": "postgraduate/computer-science-research",
        "kind": "research_replacement",
        "amount": "17500.00",
        "academic_year": "2026/27",
    },
    "C_UK003_46": {
        "old_title": "BSc (Hons) Mental Health Nursing",
        "title": "BSc (Hons) Nursing (Mental Health)",
        "location": "Chelmsford Campus",
        "slug": "undergraduate/nursing-mental-health",
        "kind": "nursing_correction",
        "code": "B760",
    },
    "C_UK003_89": {
        "old_title": "BSc (Hons) Child Nursing",
        "title": "BSc (Hons) Nursing (Children)",
        "location": "Chelmsford Campus",
        "slug": "undergraduate/nursing-child",
        "kind": "nursing_correction",
        "code": "B730",
    },
    "C_UK003_56": {
        "old_title": "MSc Horticulture",
        "title": "MSc Horticulture",
        "location": "Writtle Campus",
        "slug": "postgraduate/horticulture",
        "kind": "historical_part_time_review",
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


class Command(BaseCommand):
    help = (
        "Reconcile Computing research, two Nursing routes "
        "and Horticulture review metadata."
    )

    def add_arguments(self, parser):
        parser.add_argument("--evidence")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--accept-replacement",
            action="store_true",
            help="Accept the explicitly previewed MSc-to-MPhil replacement.",
        )

    def read_evidence(self, supplied):
        root = Path(settings.BASE_DIR)

        if supplied:
            path = Path(supplied)
            if not path.is_absolute():
                path = root / path
        else:
            candidates = sorted(
                (root / "logs" / "aru").glob(
                    "campus-intakes-*/campus-intake-evidence.json"
                ),
                reverse=True,
            )
            path = None

            for candidate in candidates:
                try:
                    data = json.loads(
                        candidate.read_text(encoding="utf-8-sig")
                    )
                    ids = {
                        item["database_course"]["course_id"]
                        for item in data.get("courses", [])
                    }
                except (OSError, ValueError, KeyError, TypeError):
                    continue

                if set(TARGETS).issubset(ids):
                    path = candidate
                    break

            if path is None:
                raise CommandError(
                    "No evidence report containing these four "
                    "courses was found. Supply --evidence."
                )

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
            or not isinstance(data.get("pages"), list)
        ):
            raise CommandError("Invalid ARU evidence report.")

        allowed_ids = {
            "C_UK003_56",
            "C_UK003_46",
            "C_UK003_89",
        }

        for error in data.get("errors", []):
            if (
                error.get("course_id") not in allowed_ids
                or error.get("error")
                != (
                    "No application link found; "
                    "manual route review required."
                )
            ):
                raise CommandError(
                    "Unexpected collection issue: "
                    + json.dumps(error, ensure_ascii=False)
                )

        return path, data

    def validate_page(self, folder, pages, course_id, target):
        url = "https://www.aru.ac.uk/study/" + target["slug"]

        matches = [
            page
            for page in pages
            if page.get("source_url", "").rstrip("/") == url
        ]
        if len(matches) != 1:
            raise CommandError(
                f"{course_id}: expected one source capture."
            )

        page = matches[0]
        filename = page.get("html_file")

        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
        ):
            raise CommandError("Invalid saved HTML filename.")

        digest = hashlib.sha256(
            (folder / filename).read_bytes()
        ).hexdigest()

        if digest != page.get("sha256"):
            raise CommandError(
                f"{course_id}: saved HTML hash mismatch."
            )

        text = " ".join(page.get("text", "").split())

        if target["kind"] == "research_replacement":
            required = [
                "Computing and Information Science MPhil, PhD",
                "January 2027",
                "Cambridge",
                "MPhil: full-time 1-3 years",
                "Bench fees",
            ]
            if not all(value in text for value in required):
                raise CommandError(
                    "Computing award, intake, duration or "
                    "additional-cost evidence changed."
                )

            amounts = {
                money(value.replace(",", ""))
                for value in re.findall(
                    r"International students,\s*2026/27\s*"
                    r"\(per year\)\s*£([\d,]+(?:\.\d{2})?)",
                    text,
                )
            }
            if amounts != {"17500.00"}:
                raise CommandError(
                    "Expected the published GBP 17,500 "
                    "annual international research fee."
                )

        elif target["kind"] == "nursing_correction":
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
                and item.get("startdate") == "2027-09"
                and any(
                    isinstance(location, dict)
                    and location.get("name") == "Chelmsford"
                    for location in item.get("location", [])
                )
                for item in instances
            )

            if not confirmed or not re.search(
                rf"UCAS course code\s+{target['code']}\b",
                text,
            ):
                raise CommandError(
                    "Standard September 2027 Chelmsford "
                    "Nursing route not confirmed."
                )

            if re.search(
                r"£[\d,]+(?:\.\d{2})?\s+"
                r"International students starting",
                text,
            ):
                raise CommandError(
                    "International Nursing fee found; review "
                    "before retaining unavailable status."
                )

        else:
            configs = []

            for script in page.get("scripts", []):
                body = script.get("text", "")
                match = re.search(
                    r"\b(?:var\s+)?configOptions\s*=\s*",
                    body,
                )
                if match:
                    try:
                        config, _ = json.JSONDecoder().raw_decode(
                            body[match.end():].lstrip()
                        )
                    except ValueError:
                        continue
                    if isinstance(config, dict):
                        configs.append(config)

            if len(configs) != 1:
                raise CommandError(
                    "Expected one Horticulture configuration."
                )

            options = configs[0].get("CourseOptions", [])

            if len(options) != 1:
                raise CommandError(
                    "Horticulture route options changed."
                )

            option = options[0]
            if (
                option.get("Duration", {}).get("Name")
                != "3 years part-time"
                or {
                    date.get("Name")
                    for date in option.get("StartDate", [])
                } != {"September"}
                or not any(
                    location.get("ShortName") == "Writtle"
                    for location in option.get("Location", [])
                )
            ):
                raise CommandError(
                    "Horticulture part-time evidence changed."
                )

            fees = option.get("TuitionFees", [])
            if (
                len(fees) != 1
                or fees[0].get("FeeBand", {}).get("Description")
                != "UK students starting 2025/26 (part-time, per year)"
                or money(
                    fees[0].get("FeeBand", {}).get("Value")
                ) != "3118.00"
            ):
                raise CommandError(
                    "Horticulture fee schedule changed; review required."
                )

        return page

    def plan(self, course, tuition, source, page, target):
        if (
            course.title
            not in {target["old_title"], target["title"]}
            or course.location != target["location"]
            or course.work_placement
        ):
            raise CommandError(
                f"{course.course_id}: unexpected stored route."
            )

        expected_levels = (
            {"MRES", "PG"}
            if target["kind"] == "research_replacement"
            else {"UG"}
            if target["kind"] == "nursing_correction"
            else {"PG"}
        )
        if course.level not in expected_levels:
            raise CommandError(
                f"{course.course_id}: unexpected stored level."
            )

        before = {
            "course": {
                field: getattr(course, field)
                for field in FIELDS
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
                    "raw_fees": copy.deepcopy(source.raw_fees),
                }
                if source
                else None
            ),
        }

        after = copy.deepcopy(before)
        after["course"]["course_url"] = page["source_url"]

        verified = target["kind"] == "research_replacement"
        academic_year = target.get("academic_year", "")

        raw = {
            "verified": verified,
            "academic_year": academic_year,
            "source_fee_category": "International",
            "currency": "GBP",
            "course_source_url": page["source_url"],
            "source_sha256": page["sha256"],
            "mapping_kind": target["kind"],
            "scholarship_status": "applicability_unconfirmed",
            "deposit_status": "applicability_unconfirmed",
            "fee_categories": [],
        }

        if verified:
            after["course"].update({
                "title": target["title"],
                "level": "PG",
                "duration": "1 to 3 Years",
                "start_month": "January",
                "start_year": 2027,
                "intake_months": "January",
            })
            after["tuition"]["tuition_fee"] = target["amount"]

            previous_raw = (
                source.raw_fees
                if source and isinstance(source.raw_fees, dict)
                else {}
            )
            raw.update({
                "annual_tuition": target["amount"],
                "study_year": 1,
                "course_kind": "postgraduate_research",
                "duration_note": (
                    "Published MPhil full-time completion "
                    "range: one to three years."
                ),
                "route_replacement": previous_raw.get(
                    "route_replacement",
                    {
                        "previous_title": target["old_title"],
                        "replacement_title": target["title"],
                        "note": (
                            "Explicit MSc-to-MPhil replacement; "
                            "not claimed to be a university-confirmed rename."
                        ),
                    },
                ),
                "additional_costs": {
                    "bench_fees_possible": True,
                    "amount": None,
                    "included_in_tuition": False,
                    "note": (
                        "Project-specific bench fees may apply. "
                        "ARU must confirm any additional amount."
                    ),
                },
                "fee_note": (
                    "Published annual international research "
                    "tuition for 2026/27, January 2027 intake. "
                    "Possible project-specific bench fees excluded."
                ),
                "fee_categories": [{
                    "source_fee_category": "International",
                    "academic_year": academic_year,
                    "currency": "GBP",
                    "annual_tuition": target["amount"],
                    "amount_requires_review": False,
                }],
            })

        elif target["kind"] == "nursing_correction":
            after["course"].update({
                "title": target["title"],
                "start_month": "September",
                "start_year": 2027,
                "intake_months": "September",
                "ucas_cao_code": target["code"],
            })
            raw.update({
                "course_kind": "undergraduate",
                "advertised_intake": "September 2027",
                "verification_reason": (
                    "The reviewed Nursing course page does not "
                    "provide a confirmed international tuition "
                    "amount. International fee and applicant "
                    "eligibility require confirmation. This "
                    "does not establish that international "
                    "applicants are excluded."
                ),
            })

        else:
            # Preserve existing duration/intake until a current
            # applicable route is established.
            raw.update({
                "course_kind": "postgraduate_taught",
                "observed_route": {
                    "duration": "3 years part-time",
                    "start_month": "September",
                    "start_year": None,
                    "campus": "Writtle",
                    "published_fee": {
                        "category": "UK",
                        "academic_year": "2025/26",
                        "amount": "3118.00",
                        "currency": "GBP",
                        "basis": "part_time_per_year",
                    },
                },
                "verification_reason": (
                    "The captured MSc Horticulture page only "
                    "provides a three-year part-time option, "
                    "September without an intake year, and a "
                    "2025/26 UK fee. A current applicable "
                    "international route and fee are unconfirmed. "
                    "The stored tuition is not verified."
                ),
            })

        if not verified:
            raw["fee_note"] = raw["verification_reason"]

        after["source"] = {
            "academic_year": academic_year,
            "source_url": page["source_url"],
            "source_course_code": target.get("code", ""),
            "source_course_name": target["title"],
            "fee_basis": "annual_tuition" if verified else "unavailable",
            "raw_fees": raw,
        }

        return {
            "course_id": course.course_id,
            "changed": before != after,
            "before": before,
            "after": after,
        }

    def handle(self, *args, **options):
        path, evidence = self.read_evidence(options["evidence"])

        try:
            pages = {
                course_id: self.validate_page(
                    path.parent,
                    evidence["pages"],
                    course_id,
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
        }

        directory = Path(settings.BASE_DIR) / "logs" / "aru"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / (
            "computing-nursing-horticulture-"
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
                raise CommandError("Expected all four active courses.")

            tuitions = list(
                Tuition.objects.select_for_update()
                .filter(course_id__in=TARGETS, residency="INTL")
                .order_by("pk")
            )
            tuition_map = {}
            for tuition in tuitions:
                if tuition.course_id in tuition_map:
                    raise CommandError("Duplicate international tuition.")
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

            # Check for a duplicate replacement before importing.
            duplicates = list(
                Course.objects.filter(
                    university_id="UK003",
                    is_active=True,
                    title=TARGETS["C_ARU003_RES_02"]["title"],
                    location="Cambridge Campus",
                )
                .exclude(course_id="C_ARU003_RES_02")
                .values_list("course_id", flat=True)
            )
            if duplicates:
                raise CommandError(
                    "Possible existing Computing MPhil duplicate: "
                    + ", ".join(duplicates)
                )

            for course_id, target in TARGETS.items():
                tuition = tuition_map[course_id]
                source = sources.get(tuition.pk)

                if (
                    target["kind"] != "research_replacement"
                    and source
                    and isinstance(source.raw_fees, dict)
                    and source.raw_fees.get("verified") is True
                ):
                    raise CommandError(
                        f"{course_id}: already has verified fee "
                        "metadata; review before replacing it."
                    )

                report["records"].append(
                    self.plan(
                        courses[course_id],
                        tuition,
                        source,
                        pages[course_id],
                        target,
                    )
                )

            report["proposed_changes"] = sum(
                entry["changed"] for entry in report["records"]
            )

            output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            replacement_needed = (
                courses["C_ARU003_RES_02"].title
                != TARGETS["C_ARU003_RES_02"]["title"]
            )
            if (
                options["apply"]
                and replacement_needed
                and not options["accept_replacement"]
            ):
                raise CommandError(
                    "The preview includes an explicit MSc-to-MPhil "
                    "replacement. Review the report, then use "
                    "--apply --accept-replacement to save it. "
                    "No database records changed."
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

                    if TARGETS[course_id].get("amount"):
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

        report["database_changes"] = bool(
            options["apply"] and report["proposed_changes"]
        )
        report["completed"] = True
        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        for entry in report["records"]:
            state = "update" if entry["changed"] else "unchanged"
            after = entry["after"]
            verified = after["source"]["raw_fees"]["verified"]
            self.stdout.write(
                f"{entry['course_id']}: "
                f"{'verified fee' if verified else 'international fee unavailable'}"
                f" — {state}"
            )
            if verified:
                self.stdout.write(
                    f"  GBP {after['tuition']['tuition_fee']}; "
                    f"{after['source']['academic_year']}"
                )
            for field, value in after["course"].items():
                previous = entry["before"]["course"][field]
                if previous != value:
                    self.stdout.write(
                        f"  {field}: {previous} -> {value}"
                    )

        self.stdout.write(
            f"Proposed changes: {report['proposed_changes']}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Reviewed changes saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Scholarship rules, stored discounts and deposit "
            "policies unchanged. Their applicability remains pending."
        )