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
    "C_UK003_31": {
        "old_title": "BSc (Hons) Psychology with Clinical Psychology",
        "title": "BSc (Hons) Psychology with Clinical Applications",
        "slug": "undergraduate/psychology-with-clinical-applications",
        "kind": "ug",
        "level": "UG",
        "duration": "3 Years",
        "location": "Cambridge Campus",
        "month": "September",
        "academic_year": "2027/28",
        "amount": "19750.00",
        "code": "C842",
    },
    "C_ARU003_RES_04": {
        "old_title": "MSc by Research (Psychology)",
        "title": "MPhil Psychology",
        "slug": "postgraduate/psychology",
        "kind": "research",
        "level": "PG",
        "duration": "1 to 3 Years",
        "location": "Cambridge Campus",
        "month": "January",
        "academic_year": "2026/27",
        "amount": "17500.00",
        "code": "",
    },
    "C_UK003_52": {
        "old_title": "MSc Child and Adolescent Mental Wellbeing",
        "title": "MSc Child and Adolescent Mental Wellbeing",
        "slug": "postgraduate/child-and-adolescent-mental-wellbeing",
        "kind": "distance",
        "level": "PG",
        "duration": "1 Year",
        "location": "Distance learning",
        "month": "September",
        "academic_year": "",
        "amount": None,
        "code": "",
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
        "Reconcile two Psychology routes and a distance-learning "
        "route. Defaults to preview."
    )

    def add_arguments(self, parser):
        parser.add_argument("--evidence", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--accept-replacement",
            action="store_true",
            help="Accept the explicit research MSc-to-MPhil replacement.",
        )

    def validate_page(self, folder, pages, target):
        url = "https://www.aru.ac.uk/study/" + target["slug"]
        matches = [
            page
            for page in pages
            if page.get("source_url", "").rstrip("/") == url
        ]
        if len(matches) != 1:
            raise CommandError(
                f"Expected one source capture: {url}"
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

        text = " ".join(page.get("text", "").split())

        if target["kind"] == "ug":
            # This exact standard-route overview supplies the
            # September intake. JSON-LD lists January only;
            # that omission is retained in the audit metadata.
            overview = re.search(
                r"BSc \(Hons\)\s+Start date\s+(.*?)\s+"
                r"Location\s+Cambridge\s+UCAS course code\s+"
                r"C842\s+Duration\s+3 years(?=\s|$)",
                text,
            )
            if (
                overview is None
                or "September 2027" not in overview.group(1)
            ):
                raise CommandError(
                    "Standard Cambridge September Psychology "
                    "route not confirmed in the overview."
                )

            section = re.search(
                r"International students\s+BSc \(Hons\).*?"
                r"BSc \(Hons\)\s+(.*?)"
                r"(?=\s+With placement\b)",
                text,
                flags=re.S,
            )
            if section is None:
                raise CommandError(
                    "Standard Psychology fee section not found."
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
            if amounts != {"19750.00"}:
                raise CommandError(
                    f"Unexpected Psychology fee: {sorted(amounts)}"
                )

        elif target["kind"] == "research":
            required = [
                "Psychology MPhil, PhD",
                "Cambridge",
                "January 2027",
                "MPhil: full-time 1-3 years",
            ]
            if not all(value in text for value in required):
                raise CommandError(
                    "Research award, campus, intake or duration "
                    "was not confirmed."
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
                    "Expected GBP 17,500 annual research fee."
                )

        else:
            overview = re.search(
                r"MSc\s+Start date\s+(.*?)\s+"
                r"Location\s+Distance learning\s+"
                r"Award\s+MSc\s+Duration\s+1 year full-time",
                text,
            )
            if (
                overview is None
                or "September 2027" not in overview.group(1)
            ):
                raise CommandError(
                    "One-year September distance-learning "
                    "route not confirmed."
                )

            amounts = {
                money(value.replace(",", ""))
                for value in re.findall(
                    r"£([\d,]+(?:\.\d{2})?)\s+"
                    r"Distance learning students starting "
                    r"2027/28\s+\(total cost\)",
                    text,
                )
            }
            if amounts != {"9450.00"}:
                raise CommandError(
                    "Expected the published GBP 9,450 "
                    "distance-learning total."
                )

            if re.search(
                r"£[\d,]+(?:\.\d{2})?\s+"
                r"International students starting",
                text,
            ):
                raise CommandError(
                    "An explicitly international fee is present; "
                    "review before retaining unavailable status."
                )

        return page

    def plan(self, course, tuition, source, page, target):
        allowed_levels = (
            {"MRES", "PG"}
            if target["kind"] == "research"
            else {target["level"]}
        )
        allowed_locations = (
            {"Chelmsford Campus", "Distance learning"}
            if target["kind"] == "distance"
            else {"Cambridge Campus"}
        )

        if (
            course.title
            not in {target["old_title"], target["title"]}
            or course.level not in allowed_levels
            or course.location not in allowed_locations
            or course.work_placement
        ):
            raise CommandError(
                f"{course.course_id}: unexpected stored route."
            )

        if target["kind"] != "research":
            if course.duration != target["duration"]:
                raise CommandError(
                    f"{course.course_id}: unexpected duration."
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
        after["course"].update({
            "title": target["title"],
            "level": target["level"],
            "duration": target["duration"],
            "location": target["location"],
            "start_month": target["month"],
            "start_year": 2027,
            "intake_months": target["month"],
            "course_url": page["source_url"],
            "ucas_cao_code": target["code"],
        })

        verified = target["amount"] is not None
        if verified:
            after["tuition"]["tuition_fee"] = target["amount"]

        raw = {
            "verified": verified,
            "academic_year": target["academic_year"],
            "source_fee_category": "International",
            "currency": "GBP",
            "course_source_url": page["source_url"],
            "source_sha256": page["sha256"],
            "study_year": 1,
            "course_kind": (
                "undergraduate"
                if target["kind"] == "ug"
                else "postgraduate_research"
                if target["kind"] == "research"
                else "postgraduate_taught"
            ),
            "study_mode": (
                "distance_learning"
                if target["kind"] == "distance"
                else "full_time_campus"
            ),
            "scholarship_status": "applicability_unconfirmed",
            "deposit_status": "applicability_unconfirmed",
            "fee_categories": [],
        }

        if verified:
            raw["annual_tuition"] = target["amount"]
            raw["fee_note"] = (
                "Published annual international tuition for "
                f"{target['academic_year']}, "
                f"{target['month']} 2027 intake."
            )
            raw["fee_categories"] = [{
                "source_fee_category": "International",
                "academic_year": target["academic_year"],
                "currency": "GBP",
                "annual_tuition": target["amount"],
                "amount_requires_review": False,
            }]

        if target["kind"] == "ug":
            raw["mapping_kind"] = "reviewed_title_correction"
            raw["intake_evidence"] = {
                "selected_intake": "September 2027",
                "basis": "standard_route_course_overview",
                "ucas_code": "C842",
                "campus": "Cambridge",
                "structured_data_note": (
                    "JSON-LD lists January 2027 only. "
                    "The standard-route overview explicitly "
                    "advertises January and September 2027."
                ),
            }

        elif target["kind"] == "research":
            previous_raw = (
                source.raw_fees
                if source and isinstance(source.raw_fees, dict)
                else {}
            )
            raw["mapping_kind"] = "explicit_replacement"
            raw["route_replacement"] = previous_raw.get(
                "route_replacement",
                {
                    "previous_title": target["old_title"],
                    "replacement_title": target["title"],
                    "note": (
                        "Explicit MSc-to-MPhil replacement; "
                        "not claimed to be a university-confirmed rename."
                    ),
                },
            )
            raw["duration_note"] = (
                "Published full-time MPhil completion range: "
                "one to three years."
            )

        else:
            raw.update({
                "mapping_kind": "distance_learning_correction",
                "published_fee_evidence": {
                    "amount": "9450.00",
                    "currency": "GBP",
                    "academic_year": "2027/28",
                    "category": "Distance learning",
                    "basis": "total_course_fee",
                    "international_applicability_confirmed": False,
                },
                "verification_reason": (
                    "A GBP 9,450 total distance-learning course "
                    "fee is published for 2027/28, but its "
                    "applicability to international students "
                    "has not been confirmed. The stored numeric "
                    "tuition is not verified."
                ),
                "deposit_status": (
                    "campus_pre_cas_policy_not_applicable"
                ),
                "deposit_note": (
                    "This is distance learning. Do not apply "
                    "the campus pre-CAS deposit policy. "
                    "Any online-course payment requirements "
                    "must be confirmed separately."
                ),
            })
            raw["fee_note"] = raw["verification_reason"]

        after["source"] = {
            "academic_year": target["academic_year"],
            "source_url": page["source_url"],
            "source_course_code": target["code"],
            "source_course_name": target["title"],
            "fee_basis": (
                "annual_tuition" if verified else "unavailable"
            ),
            "raw_fees": raw,
        }

        return {
            "course_id": course.course_id,
            "changed": before != after,
            "before": before,
            "after": after,
        }

    def handle(self, *args, **options):
        path = Path(options["evidence"])
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path

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

        folder = Path(settings.BASE_DIR) / "logs" / "aru"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / (
            "psychology-distance-"
            + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )
        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
            "evidence_report": str(path),
            "records": [],
        }

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
                    "Expected all three active course records."
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

            for course_id, target in TARGETS.items():
                duplicates = list(
                    Course.objects.filter(
                        university_id="UK003",
                        is_active=True,
                        title=target["title"],
                        location=target["location"],
                    )
                    .exclude(course_id=course_id)
                    .values_list("course_id", flat=True)
                )
                if duplicates:
                    raise CommandError(
                        f"{course_id}: possible duplicate route: "
                        + ", ".join(duplicates)
                    )

                tuition = tuition_map[course_id]
                source = sources.get(tuition.pk)

                if (
                    target["kind"] == "distance"
                    and source
                    and isinstance(source.raw_fees, dict)
                    and source.raw_fees.get("verified") is True
                ):
                    raise CommandError(
                        "Distance-learning course already has "
                        "verified fee metadata; review before replacing it."
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
                courses["C_ARU003_RES_04"].title
                != TARGETS["C_ARU003_RES_04"]["title"]
            )
            if (
                options["apply"]
                and replacement_needed
                and not options["accept_replacement"]
            ):
                raise CommandError(
                    "Review the explicit MPhil replacement, then "
                    "use --apply --accept-replacement. "
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

                    if TARGETS[course_id]["amount"] is not None:
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
            "policies unchanged."
        )