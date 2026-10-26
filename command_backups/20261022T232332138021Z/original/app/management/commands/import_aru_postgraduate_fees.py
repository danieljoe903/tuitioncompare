import copy
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


YEAR = "2027/28"
BASE = "https://www.aru.ac.uk/study/postgraduate/"

# ID suffix, slug, stored title, source heading, campus, annual fee.
BATCH = [
    (
        "21", "artificial-intelligence",
        "MSc Artificial Intelligence", "Artificial Intelligence",
        "Cambridge", "20925.00",
    ),
    (
        "28", "cyber-security",
        "MSc Cyber Security", "Cyber Security",
        "Cambridge", "20925.00",
    ),
    (
        "47", "applied-bioscience",
        "MSc Applied Bioscience", "Applied Bioscience",
        "Cambridge", "20925.00",
    ),
    (
        "48", "advanced-biomedical-science",
        "MSc Advanced Biomedical Science",
        "Advanced Biomedical Science",
        "Cambridge", "20925.00",
    ),
    (
        "49", "automation-and-robotics",
        "MSc Automation and Robotics", "Automation and Robotics",
        "Chelmsford", "20925.00",
    ),
    (
        "50", "brand-management",
        "MSc Brand Management", "Brand Management",
        "Cambridge", "20925.00",
    ),
    (
        "51", "business-data-analytics",
        "MSc Business Data Analytics", "Business Data Analytics",
        "Peterborough", "20925.00",
    ),
    (
        "53", "clinical-child-psychology",
        "MSc Clinical Child Psychology", "Clinical Child Psychology",
        "Cambridge", "20925.00",
    ),
    (
        "72", "engineering-management",
        "MSc Engineering Management", "Engineering Management",
        "Chelmsford", "20925.00",
    ),
    (
        "75", "computer-science",
        "MSc Computer Science", "Computer Science",
        "Cambridge", "20925.00",
    ),
    (
        "77", "pharmaceutical-science",
        "MSc Pharmaceutical Science", "Pharmaceutical Science",
        "Chelmsford", "19750.00",
    ),
    (
        "78", "forensic-science",
        "MSc Forensic Science", "Forensic Science",
        "Cambridge", "20925.00",
    ),
    (
        "79", "applied-wildlife-conservation",
        "MSc Applied Wildlife Conservation",
        "Applied Wildlife Conservation",
        "Cambridge", "20925.00",
    ),
    (
        "80", "agribusiness",
        "Agribusiness MBA", "Agribusiness MBA",
        "Writtle", "18825.00",
    ),
    (
        "103", "town-planning",
        "MSc Town Planning", "Town Planning",
        "Chelmsford", "20925.00",
    ),
]


def normalise_url(value):
    return value.rstrip("/")


def snapshot_source(source):
    if source is None:
        return None

    return {
        name: copy.deepcopy(getattr(source, name))
        for name in (
            "academic_year",
            "source_url",
            "source_course_code",
            "source_course_name",
            "fee_basis",
            "raw_fees",
        )
    }


class Command(BaseCommand):
    help = (
        "Preview or apply 15 reviewed ARU postgraduate "
        "international annual fees for September 2027."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--report",
            help="Path to the collected course-evidence.json.",
        )

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        logs = root / "logs" / "aru"
        logs.mkdir(parents=True, exist_ok=True)

        if options["report"]:
            evidence_path = Path(options["report"])
            if not evidence_path.is_absolute():
                evidence_path = root / evidence_path
        else:
            files = list(
                logs.glob("courses-*/course-evidence.json")
            )
            if not files:
                raise CommandError(
                    "Run inspect_aru_courses first."
                )
            evidence_path = max(
                files, key=lambda item: item.parent.name
            )

        try:
            evidence = json.loads(
                evidence_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError) as exc:
            raise CommandError(
                f"Cannot read course evidence: {exc}"
            ) from exc

        if evidence.get("university_id") != "UK003":
            raise CommandError("Evidence is not for UK003.")

        # Use requested URLs to avoid confusing redirected pages.
        pages = {}
        for page in evidence.get("pages", []):
            requested = page.get("requested_url")
            if not requested:
                continue

            key = normalise_url(requested)
            if key in pages:
                raise CommandError(
                    f"Duplicate requested-page evidence: {key}"
                )
            pages[key] = page

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        output = logs / f"pg-import-{stamp}.json"

        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
            "university_id": "UK003",
            "academic_year": YEAR,
            "evidence_report": str(evidence_path.resolve()),
            "records": [],
            "blocked": [],
        }

        def save_report():
            output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        with transaction.atomic():
            pending = []

            for (
                suffix, slug, stored_title,
                heading, campus, expected_fee
            ) in BATCH:
                course_id = f"C_UK003_{suffix}"
                url = BASE + slug

                try:
                    page = pages.get(normalise_url(url))
                    if page is None:
                        raise ValueError("Course evidence is missing.")

                    if normalise_url(
                        page.get("source_url", "")
                    ) != normalise_url(url):
                        raise ValueError(
                            "The page redirected to a different route."
                        )

                    if not page.get("sha256"):
                        raise ValueError("Source hash is missing.")

                    if page.get("title", "").strip() != heading:
                        raise ValueError(
                            "The published course heading differs."
                        )

                    if course_id not in page.get(
                        "candidate_course_ids", []
                    ):
                        raise ValueError(
                            "The page is not a recorded candidate "
                            "for this course."
                        )

                    body = page.get("text", "").split(
                        "End of main navigation."
                    )[-1]

                    route = re.search(
                        r"Start date\s+(.*?)\s+Location\s+(.*?)"
                        r"\s+Award\s+(.*?)\s+Duration"
                        r"\s+1 year full-time\b",
                        body,
                        flags=re.IGNORECASE,
                    )

                    if route is None:
                        raise ValueError(
                            "The standard one-year full-time "
                            "route is not confirmed."
                        )

                    if "September 2027" not in route.group(1):
                        raise ValueError(
                            "September 2027 intake is not confirmed."
                        )

                    if route.group(2).strip().casefold() != campus.casefold():
                        raise ValueError(
                            "The advertised campus differs."
                        )

                    award = "MBA" if suffix == "80" else "MSc"
                    if route.group(3).strip() != award:
                        raise ValueError(
                            "The advertised award differs."
                        )

                    fee_marker = body.rfind("Fees and funding")
                    if fee_marker < 0:
                        raise ValueError(
                            "The fee section is missing."
                        )

                    amounts = {
                        Decimal(value.replace(",", ""))
                        for value in re.findall(
                            r"£\s*([\d,]+(?:\.\d{2})?)"
                            r"\s+International students starting"
                            r"\s+2027/28"
                            r"\s+\(full-time,\s*per year\)",
                            body[fee_marker:],
                            flags=re.IGNORECASE,
                        )
                    }

                    amount = Decimal(expected_fee)
                    if amounts != {amount}:
                        raise ValueError(
                            "The international annual fee differs "
                            f"or is ambiguous: {sorted(amounts)}."
                        )

                    course = (
                        Course.objects.select_for_update()
                        .get(
                            course_id=course_id,
                            university_id="UK003",
                        )
                    )

                    if (
                        course.title != stored_title
                        or course.level not in {"PG", "PGT"}
                        or course.duration.strip().casefold()
                        != "1 year"
                        or not course.is_active
                        or course.location.split()[0].casefold()
                        != campus.casefold()
                    ):
                        raise ValueError(
                            "Stored title, level, duration, campus "
                            "or active status needs review."
                        )

                    tuitions = list(
                        Tuition.objects.select_for_update()
                        .filter(course=course, residency="INTL")
                    )

                    if (
                        len(tuitions) != 1
                        or tuitions[0].currency != "GBP"
                    ):
                        raise ValueError(
                            "Exactly one GBP international "
                            "tuition record is required."
                        )

                    tuition = tuitions[0]
                    source = (
                        TuitionSource.objects.select_for_update()
                        .filter(tuition=tuition)
                        .first()
                    )

                    before_course = {
                        name: getattr(course, name)
                        for name in (
                            "title", "level", "duration",
                            "location", "start_month",
                            "start_year", "course_url",
                        )
                    }

                    before = {
                        "course": before_course,
                        "tuition_fee": format(
                            Decimal(str(tuition.tuition_fee)),
                            ".2f",
                        ),
                        "source": snapshot_source(source),
                    }

                    after_course = {
                        **before_course,
                        "start_month": "September",
                        "start_year": 2027,
                        "course_url": url,
                    }

                    raw = {
                        "verified": True,
                        "academic_year": YEAR,
                        "source_fee_category": "International",
                        "course_kind": "postgraduate_taught",
                        "study_year": 1,
                        "study_mode": "full_time",
                        "annual_tuition": str(amount),
                        "currency": "GBP",
                        "course_source_url": url,
                        "source_sha256": page["sha256"],
                        "source_checked_at": page.get("checked_at"),
                        "fee_method": "reviewed_named_course_fee",
                        "scholarship_status": "pending_separate_review",
                        "deposit_status": "pending_scope_extension",
                        "fee_note": (
                            "Published 2027/28 international annual "
                            "fee for the standard September 2027 "
                            "one-year full-time course. Optional "
                            "professional-experience routes are "
                            "excluded."
                        ),
                        "fee_categories": [{
                            "source_fee_category": "International",
                            "academic_year": YEAR,
                            "currency": "GBP",
                            "annual_tuition": str(amount),
                            "amount_requires_review": False,
                        }],
                    }

                    after_source = {
                        "academic_year": YEAR,
                        "source_url": url,
                        "source_course_code": "",
                        "source_course_name": heading,
                        "fee_basis": "annual_tuition",
                        "raw_fees": raw,
                    }

                    after = {
                        "course": after_course,
                        "tuition_fee": str(amount),
                        "source": after_source,
                    }

                    record = {
                        "course_id": course_id,
                        "changed": before != after,
                        "before": before,
                        "after": after,
                    }
                    report["records"].append(record)
                    pending.append((course, tuition, record))

                except (ValueError, Course.DoesNotExist) as exc:
                    report["blocked"].append({
                        "course_id": course_id,
                        "reason": str(exc),
                    })

            if report["blocked"]:
                save_report()
                raise CommandError(
                    f"{len(report['blocked'])} records blocked. "
                    f"No changes applied. Report: {output}"
                )

            if options["apply"]:
                for course, tuition, record in pending:
                    if not record["changed"]:
                        continue

                    after = record["after"]
                    changed_fields = []

                    for name, value in after["course"].items():
                        if getattr(course, name) != value:
                            setattr(course, name, value)
                            changed_fields.append(name)

                    if changed_fields:
                        course.save(
                            update_fields=[
                                *changed_fields, "updated_at"
                            ]
                        )

                    amount = Decimal(after["tuition_fee"])
                    if tuition.tuition_fee != amount:
                        tuition.tuition_fee = amount
                        tuition.save(update_fields=["tuition_fee"])

                    TuitionSource.objects.update_or_create(
                        tuition=tuition,
                        defaults=after["source"],
                    )

                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=course.course_id,
                        residency="INTL",
                        before=record["before"],
                        after=after,
                    )

                report["database_changes"] = any(
                    row["changed"]
                    for row in report["records"]
                )

        save_report()

        for record in report["records"]:
            self.stdout.write(
                f"{record['course_id']}: GBP "
                f"{record['before']['tuition_fee']} -> "
                f"{record['after']['tuition_fee']}; "
                "September 2027 — "
                f"{'update' if record['changed'] else 'unchanged'}"
            )

        self.stdout.write(
            f"\nMatched records: {len(report['records'])}"
        )
        self.stdout.write(
            "Proposed/updated records: "
            f"{sum(row['changed'] for row in report['records'])}"
        )
        self.stdout.write(
            f"Blocked: {len(report['blocked'])}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Reviewed tuition, intake and source updates applied."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Scholarships and deposit scope require separate "
            "reconciliation. Stored discounts and deposits unchanged."
        )