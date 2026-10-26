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
    "C_ARU003_RES_05": {
        "old_title": "MPhil (Research) Business",
        "title": "MPhil Business — School of Management",
        "location": "Chelmsford Campus",
        "source_heading": (
            "Business PhD - School of Management MPhil, PhD"
        ),
        "slug": "business-and-management",
        "route_note": (
            "Explicitly selected the School of Management route. "
            "This is distinct from the School of Economics, "
            "Finance and Law research route."
        ),
    },
    "C_ARU003_RES_06": {
        "old_title": "MPhil (Research) Creative Arts & Design",
        "title": "MPhil Art and Design",
        "location": "Cambridge Campus",
        "source_heading": "Art and Design MPhil, PhD",
        "slug": "art-and-design-research",
        "route_note": (
            "Explicit replacement of the broad Creative Arts "
            "& Design record with the advertised Art and Design route."
        ),
    },
    "C_ARU003_RES_08": {
        "old_title": "MPhil (Research) Music & Performing Arts",
        "title": "MPhil Musicology",
        "location": "Cambridge Campus",
        "source_heading": "Musicology MPhil, PhD",
        "slug": "musicology-research",
        "route_note": (
            "Explicit replacement with Musicology, a narrower "
            "subject than the previous Music & Performing Arts record."
        ),
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
    "work_placement",
]


def money(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid monetary amount.")
    return f"{amount:.2f}"


class Command(BaseCommand):
    help = (
        "Preview or apply three explicit ARU MPhil replacements "
        "with January 2027 international research fees."
    )

    def add_arguments(self, parser):
        parser.add_argument("--evidence")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--accept-replacements",
            action="store_true",
            help="Accept the three explicitly described replacements.",
        )

    def read_evidence(self, supplied):
        root = Path(settings.BASE_DIR)

        if supplied:
            path = Path(supplied)
            if not path.is_absolute():
                path = root / path
        else:
            path = None
            candidates = sorted(
                (root / "logs" / "aru").glob(
                    "campus-intakes-*/campus-intake-evidence.json"
                ),
                reverse=True,
            )

            for candidate in candidates:
                try:
                    data = json.loads(
                        candidate.read_text(encoding="utf-8-sig")
                    )
                    ids = {
                        item["database_course"]["course_id"]
                        for item in data.get("courses", [])
                    }
                except (OSError, ValueError, TypeError, KeyError):
                    continue

                if set(TARGETS).issubset(ids):
                    path = candidate
                    break

            if path is None:
                raise CommandError(
                    "No matching three-course evidence found. "
                    "Supply --evidence."
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
            or data.get("errors")
            or not isinstance(data.get("pages"), list)
        ):
            raise CommandError(
                "Invalid evidence or collection errors present."
            )

        return path, data

    def validate_page(self, folder, pages, target):
        url = (
            "https://www.aru.ac.uk/study/postgraduate/"
            + target["slug"]
        )

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
        marker = "End of main navigation."

        if marker not in text:
            raise CommandError(
                "Cannot isolate the research course page."
            )

        overview = text.split(marker, 1)[1].split(
            "Apply online", 1
        )[0]

        campus = target["location"].replace(" Campus", "")
        required = [
            target["source_heading"],
            campus,
            "January 2027",
        ]

        if not all(value in overview for value in required):
            raise CommandError(
                "Research award, campus or intake not confirmed."
            )

        if "MPhil: full-time 1-3 years" not in text:
            raise CommandError(
                "Published MPhil duration range not confirmed."
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
                f"Unexpected international research fee: "
                f"{sorted(amounts)}"
            )

        if "Bench fees" not in text:
            raise CommandError(
                "Expected project-specific bench-fee note missing."
            )

        return page

    def plan_record(self, course, tuition, source, page, target):
        if (
            course.title
            not in {target["old_title"], target["title"]}
            or course.level not in {"MRES", "PG"}
            or course.location != target["location"]
            or course.work_placement
        ):
            raise CommandError(
                f"{course.course_id}: unexpected stored route."
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
            "level": "PG",
            "duration": "1 to 3 Years",
            "start_month": "January",
            "start_year": 2027,
            "intake_months": "January",
            "course_url": page["source_url"],
        })
        after["tuition"]["tuition_fee"] = "17500.00"

        previous_raw = (
            source.raw_fees
            if source and isinstance(source.raw_fees, dict)
            else {}
        )

        raw = {
            "verified": True,
            "academic_year": "2026/27",
            "source_fee_category": "International",
            "currency": "GBP",
            "annual_tuition": "17500.00",
            "study_year": 1,
            "course_kind": "postgraduate_research",
            "study_mode": "full_time_campus",
            "course_source_url": page["source_url"],
            "source_sha256": page["sha256"],
            "source_heading": target["source_heading"],
            "campus": target["location"],
            "intake": "January 2027",
            "mapping_kind": "explicit_replacement",
            "route_replacement": previous_raw.get(
                "route_replacement",
                {
                    "previous_title": target["old_title"],
                    "replacement_title": target["title"],
                    "note": target["route_note"],
                    "university_confirmed_rename": False,
                },
            ),
            "duration_note": (
                "Published full-time MPhil completion range: "
                "one to three years."
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
            "scholarship_status": (
                "research_applicability_unconfirmed"
            ),
            "deposit_status": "research_deposit_unconfirmed",
            "award_review_note": (
                "No applicable scholarship deduction or research "
                "deposit amount has been verified for this "
                "January 2027 route. Confirm with ARU."
            ),
            "fee_note": (
                "Published annual international research tuition "
                "for 2026/27, January 2027 intake. Possible "
                "project-specific bench fees are excluded."
            ),
            "fee_categories": [{
                "source_fee_category": "International",
                "academic_year": "2026/27",
                "currency": "GBP",
                "annual_tuition": "17500.00",
                "amount_requires_review": False,
            }],
        }

        after["source"] = {
            "academic_year": "2026/27",
            "source_url": page["source_url"],
            "source_course_code": "",
            "source_course_name": target["title"],
            "fee_basis": "annual_tuition",
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
            "three-research-routes-"
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
                    "Expected all three active research records."
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
                        f"{course_id}: possible duplicate: "
                        + ", ".join(duplicates)
                    )

                tuition = tuition_map[course_id]
                report["records"].append(
                    self.plan_record(
                        courses[course_id],
                        tuition,
                        sources.get(tuition.pk),
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

            replacements_needed = any(
                courses[course_id].title != target["title"]
                for course_id, target in TARGETS.items()
            )

            if (
                options["apply"]
                and replacements_needed
                and not options["accept_replacements"]
            ):
                raise CommandError(
                    "Review the three explicit replacements, then "
                    "use --apply --accept-replacements. "
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

                    tuition.tuition_fee = Decimal("17500.00")
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
            self.stdout.write(
                f"{entry['course_id']}: GBP "
                f"{entry['before']['tuition']['tuition_fee']} "
                f"-> 17500.00; January 2027 / 2026/27 — {state}"
            )
            for field, value in entry["after"]["course"].items():
                old = entry["before"]["course"][field]
                if old != value:
                    self.stdout.write(
                        f"  {field}: {old} -> {value}"
                    )

        self.stdout.write(
            f"Proposed changes: {report['proposed_changes']}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Reviewed replacements and research fees saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Scholarship and research deposit applicability "
            "recorded as pending. Stored discounts and "
            "deposit policies unchanged."
        )