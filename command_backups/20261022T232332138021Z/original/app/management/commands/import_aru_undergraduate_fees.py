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


BASE = "https://www.aru.ac.uk/study/undergraduate/"
ACADEMIC_YEAR = "2027/28"

# Reviewed standard routes, excluding foundation/placement years.
# course suffix, page slug, award, subject, campus, UCAS, annual fee
REVIEWED = [
    ("91", "acting", "BA", "Acting",
     "Cambridge", "W410", "18825.00"),
    ("17", "agriculture", "BSc", "Agriculture",
     "Writtle", "D401", "18825.00"),
    ("69", "agriculture-with-business", "BSc",
     "Agriculture with Business", "Writtle", "D403", "18825.00"),
    ("42", "biology", "BSc", "Biology",
     "Cambridge", "C100", "20925.00"),
    ("04", "biomedical-science", "BSc", "Biomedical Science",
     "Cambridge", "B940", "19750.00"),
    ("62", "building-surveying", "BSc", "Building Surveying",
     "Chelmsford", "K230", "19750.00"),
    ("34", "computer-networking", "BSc", "Computer Networking",
     "Peterborough", "I120", "19750.00"),
    ("07", "criminology", "BA", "Criminology",
     "Cambridge", "L370", "18825.00"),
    ("84", "criminology-and-policing", "BA",
     "Criminology and Policing", "Cambridge", "L311", "18825.00"),
    ("08", "cyber-security", "BSc", "Cyber Security",
     "Cambridge", "I103", "19750.00"),
    ("33", "cyber-security-with-forensic-computing", "BSc",
     "Cyber Security and Forensic Computing",
     "Peterborough", "I114", "19750.00"),
    ("09", "drama", "BA", "Drama",
     "Cambridge", "W490", "18825.00"),
    ("38", "electronic-and-electrical-engineering-beng", "BEng",
     "Electronic and Electrical Engineering",
     "Chelmsford", "H609", "19750.00"),
    ("67", "film-and-media", "BA", "Film and Media",
     "Cambridge", "P391", "18825.00"),
    ("11", "fine-art", "BA", "Fine Art",
     "Cambridge", "W105", "19750.00"),
    ("68", "graphic-design", "BA", "Graphic Design",
     "Cambridge", "W200", "18825.00"),
    ("86", "history", "BA", "History",
     "Cambridge", "V140", "18825.00"),
    ("35", "international-business-management", "BSc",
     "International Business Management",
     "Cambridge", "N102", "18825.00"),
    ("39", "mechatronics-and-robotics-engineering-beng", "BEng",
     "Mechatronics and Robotics Engineering",
     "Chelmsford", "H361", "19750.00"),
    ("85", "philosophy", "BA", "Philosophy",
     "Cambridge", "V501", "18825.00"),
    ("14", "psychology", "BSc", "Psychology",
     "Cambridge", "C800", "19750.00"),
    ("32", "psychology-with-education", "BSc",
     "Psychology with Education",
     "Cambridge", "C802", "19750.00"),
    ("83", "sociology", "BA", "Sociology",
     "Cambridge", "L300", "18825.00"),
    ("63", "software-development", "BSc", "Software Development",
     "Peterborough", "I303", "19750.00"),
]


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise CommandError(f"Cannot read {path}: {exc}") from exc


def canonical_url(value):
    return value.rstrip("/")


def course_snapshot(course):
    fields = [
        "title",
        "level",
        "duration",
        "location",
        "start_month",
        "start_year",
        "course_url",
        "ucas_cao_code",
        "is_active",
    ]
    return {name: getattr(course, name) for name in fields}


def source_snapshot(source):
    if source is None:
        return None

    return {
        "academic_year": source.academic_year,
        "source_url": source.source_url,
        "source_course_code": source.source_course_code,
        "source_course_name": source.source_course_name,
        "fee_basis": source.fee_basis,
        "raw_fees": source.raw_fees,
    }


class Command(BaseCommand):
    help = (
        "Preview or apply 24 reviewed ARU undergraduate "
        "international fees from collected course evidence."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--report",
            help="Path to course-evidence.json.",
        )

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        log_root = root / "logs" / "aru"
        log_root.mkdir(parents=True, exist_ok=True)

        if options["report"]:
            evidence_path = Path(options["report"])
            if not evidence_path.is_absolute():
                evidence_path = root / evidence_path
        else:
            files = list(
                log_root.glob("courses-*/course-evidence.json")
            )
            if not files:
                raise CommandError(
                    "Run inspect_aru_courses first."
                )
            evidence_path = max(
                files, key=lambda path: path.parent.name
            )

        evidence = read_json(evidence_path)

        if evidence.get("university_id") != "UK003":
            raise CommandError("Evidence is not for UK003.")

        pages = {}

        for page in evidence.get("pages", []):
            for value in [
                page.get("requested_url"),
                page.get("source_url"),
            ]:
                if value:
                    key = canonical_url(value)
                    existing = pages.get(key)

                    if (
                        existing is not None
                        and existing.get("sha256")
                        != page.get("sha256")
                    ):
                        raise CommandError(
                            f"Conflicting evidence for {key}."
                        )
                    pages[key] = page

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        output = log_root / f"ug-import-{stamp}.json"

        report = {
            "generated_at": timezone.now().isoformat(),
            "university_id": "UK003",
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
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
            for (
                suffix, slug, award, subject,
                campus, ucas, expected_fee
            ) in REVIEWED:
                course_id = f"C_UK003_{suffix}"
                url = BASE + slug

                try:
                    page = pages.get(canonical_url(url))

                    if page is None:
                        raise ValueError("Course evidence is missing.")

                    if (
                        canonical_url(page.get("source_url", ""))
                        != canonical_url(url)
                    ):
                        raise ValueError(
                            "The course page redirected to another route."
                        )

                    if not page.get("sha256"):
                        raise ValueError("Source hash is missing.")

                    if course_id not in page.get(
                        "candidate_course_ids", []
                    ):
                        raise ValueError(
                            "The evidence does not identify this candidate."
                        )

                    if page.get("title", "").strip() != subject:
                        raise ValueError(
                            "The published subject title differs."
                        )

                    body = page.get("text", "").split(
                        "End of main navigation."
                    )[-1]

                    # First route summary, before optional routes.
                    route = re.search(
                        r"Start date\s+(.*?)\s+Location\s+(.*?)"
                        r"\s+UCAS course code\s+(.*?)"
                        r"\s+Duration\s+3 years\b",
                        body,
                        flags=re.IGNORECASE,
                    )

                    if route is None:
                        raise ValueError(
                            "Standard three-year route not confirmed."
                        )

                    if "September 2027" not in route.group(1):
                        raise ValueError(
                            "September 2027 intake is not confirmed."
                        )

                    locations = route.group(2).strip()
                    if locations.casefold() != campus.casefold():
                        raise ValueError(
                            f"Expected campus {campus}; found {locations}."
                        )

                    if route.group(3).strip().upper() != ucas:
                        raise ValueError("UCAS route code differs.")

                    prefix = body[:route.start()]
                    if not re.search(
                        rf"\b{re.escape(award)}\s+\(Hons\)",
                        prefix,
                    ):
                        raise ValueError(
                            "Expected honours award is not confirmed."
                        )

                    # Read fees only from the actual fee section.
                    marker = body.rfind("Fees and funding")
                    if marker < 0:
                        raise ValueError("Fee section was not found.")

                    fee_text = body[marker:]
                    amounts = {
                        Decimal(value.replace(",", ""))
                        for value in re.findall(
                            r"£\s*([\d,]+(?:\.\d{2})?)"
                            r"\s+International students starting"
                            r"\s+2027/28\s+\(full-time,\s*per year\)",
                            fee_text,
                            flags=re.IGNORECASE,
                        )
                    }

                    amount = Decimal(expected_fee)

                    if amounts != {amount}:
                        raise ValueError(
                            "Published 2027/28 annual fee differs "
                            f"or is ambiguous: {sorted(amounts)}."
                        )

                    course = (
                        Course.objects.select_for_update()
                        .get(
                            course_id=course_id,
                            university_id="UK003",
                        )
                    )

                    expected_title = f"{award} (Hons) {subject}"

                    if (
                        course.title != expected_title
                        or course.level != "UG"
                        or course.duration.strip().casefold() != "3 years"
                        or not course.is_active
                        or course.location.split()[0].casefold()
                        != campus.casefold()
                    ):
                        raise ValueError(
                            "Stored course identity, duration, "
                            "campus or active status needs review."
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
                        raise ValueError(
                            "Exactly one GBP international tuition "
                            "record is required."
                        )

                    tuition = tuitions[0]
                    source = (
                        TuitionSource.objects.select_for_update()
                        .filter(tuition=tuition)
                        .first()
                    )

                    before = {
                        "course": course_snapshot(course),
                        "tuition_fee": str(
                            Decimal(str(tuition.tuition_fee)).quantize(
                                Decimal("0.01")
                            )
                        ),
                        "source": source_snapshot(source),
                    }

                    after_course = {
                        **before["course"],
                        "start_month": "September",
                        "start_year": 2027,
                        "course_url": url,
                        "ucas_cao_code": ucas,
                    }

                    raw = {
                        "verified": True,
                        "academic_year": ACADEMIC_YEAR,
                        "source_fee_category": "International",
                        "course_kind": "undergraduate_taught",
                        "study_year": 1,
                        "study_mode": "full_time",
                        "annual_tuition": str(amount),
                        "currency": "GBP",
                        "course_source_url": url,
                        "source_sha256": page["sha256"],
                        "source_checked_at": page.get("checked_at"),
                        "fee_method": "reviewed_named_course_fee",
                        "scholarship_status": "pending_separate_review",
                        "deposit_status": "pending_policy_import",
                        "fee_note": (
                            "Published annual international fee for "
                            "2027/28, standard three-year honours route. "
                            "Optional foundation and placement routes "
                            "are excluded from this comparison."
                        ),
                        "fee_categories": [{
                            "source_fee_category": "International",
                            "academic_year": ACADEMIC_YEAR,
                            "currency": "GBP",
                            "annual_tuition": str(amount),
                            "amount_requires_review": False,
                        }],
                    }

                    after_source = {
                        "academic_year": ACADEMIC_YEAR,
                        "source_url": url,
                        "source_course_code": ucas,
                        "source_course_name": subject,
                        "fee_basis": "annual_tuition",
                        "raw_fees": raw,
                    }

                    after = {
                        "course": after_course,
                        "tuition_fee": str(amount),
                        "source": after_source,
                    }

                    changed = before != after

                    report["records"].append({
                        "course_id": course_id,
                        "changed": changed,
                        "before": before,
                        "after": after,
                    })

                except (
                    ValueError,
                    Course.DoesNotExist,
                ) as exc:
                    report["blocked"].append({
                        "course_id": course_id,
                        "reason": str(exc),
                    })

            # Validate every record before changing any record.
            if report["blocked"]:
                save_report()
                raise CommandError(
                    f"{len(report['blocked'])} records blocked. "
                    f"No changes applied. Review: {output}"
                )

            if options["apply"]:
                for record in report["records"]:
                    if not record["changed"]:
                        continue

                    course = Course.objects.get(
                        course_id=record["course_id"]
                    )
                    tuition = Tuition.objects.get(
                        course=course,
                        residency="INTL",
                        currency="GBP",
                    )
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

                    new_fee = Decimal(after["tuition_fee"])
                    if tuition.tuition_fee != new_fee:
                        tuition.tuition_fee = new_fee
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
                    row["changed"] for row in report["records"]
                )

        save_report()

        for record in report["records"]:
            self.stdout.write(
                f"{record['course_id']}: GBP "
                f"{record['before']['tuition_fee']} -> "
                f"{record['after']['tuition_fee']}; "
                f"September 2027 — "
                f"{'update' if record['changed'] else 'unchanged'}"
            )

        self.stdout.write(
            f"\nMatched records: {len(report['records'])}"
        )
        self.stdout.write(
            "Proposed/updated records: "
            f"{sum(row['changed'] for row in report['records'])}"
        )
        self.stdout.write(f"Report: {output}")

        if options["apply"]:
            self.stdout.write(
                "Reviewed tuition, intake and source updates applied."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )

        self.stdout.write(
            "Scholarships and deposit policies require "
            "separate reconciliation."
        )