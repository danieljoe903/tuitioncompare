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
BASE = "https://www.aru.ac.uk/study/undergraduate/"

# ID suffix, slug, subject, old award, published award,
# stored campus, published campus, UCAS, annual fee.
BATCH = [
    (
        "03", "architecture", "Architecture",
        "BSc", "BA", "Chelmsford", "Chelmsford",
        "K100", "19750.00",
    ),
    (
        "16", "computer-games-design", "Computer Games Design",
        "BSc", "BA", "Cambridge", "Cambridge",
        "I620", "18825.00",
    ),
    (
        "06", "computer-science-bsc", "Computer Science",
        "BSc", "BSc", "Cambridge", "Cambridge",
        "I106", "19750.00",
    ),
    (
        "10", "primary-education-studies",
        "Primary Education Studies",
        "BA", "BA", "Chelmsford", "Chelmsford",
        "XX12", "18825.00",
    ),
    (
        "15", "sport-and-exercise-science",
        "Sport and Exercise Science",
        "BSc", "BSc", "Cambridge", "Cambridge",
        "C600", "19750.00",
    ),
    (
        "90", "sport-coaching-and-physical-education",
        "Sport Coaching and Physical Education",
        "BSc", "BSc", "Cambridge", "Cambridge",
        "C602", "19750.00",
    ),
    (
        "37", "mechanical-engineering-beng",
        "Mechanical Engineering",
        "BEng", "BEng", "Chelmsford", "Chelmsford",
        "H300", "19750.00",
    ),
    (
        "93", "interior-design", "Interior Design",
        "BA", "BA", "Chelmsford", "Cambridge",
        "W250", "19750.00",
    ),
]

COURSE_FIELDS = (
    "title",
    "level",
    "duration",
    "location",
    "start_month",
    "start_year",
    "course_url",
    "ucas_cao_code",
    "is_active",
)

SOURCE_FIELDS = (
    "academic_year",
    "source_url",
    "source_course_code",
    "source_course_name",
    "fee_basis",
    "raw_fees",
)


def snapshot(obj, fields):
    if obj is None:
        return None
    return {
        name: copy.deepcopy(getattr(obj, name))
        for name in fields
    }


def standard_fee_evidence(body):
    """
    Return the standard international route's fee block.

    Optional placement, foundation, accelerated and part-time
    sections are excluded.
    """
    marker = body.rfind("Fees and funding")
    if marker < 0:
        raise ValueError("Fee section is missing.")

    section = body[marker:]
    international = re.search(
        r"\bInternational students\b", section
    )
    if international is None:
        raise ValueError("International fee section is missing.")

    section = section[international.end():]

    first_price = section.find("£")
    if first_price < 0:
        raise ValueError("No international price was captured.")

    # Route-tab labels occur before the first price.
    section = section[first_price:]

    boundaries = [
        " With placement",
        " With foundation",
        " 2 years accelerated",
        " Part-time",
        " 5 years",
        " Placement year fee",
        " Additional course costs",
        " Living costs",
        " Apply for ",
    ]

    ends = [
        position
        for boundary in boundaries
        if (position := section.find(boundary)) >= 0
    ]

    return section[:min(ends)] if ends else section


class Command(BaseCommand):
    help = (
        "Preview or apply eight reviewed ARU undergraduate "
        "award, campus, intake and international fee corrections."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--report",
            help="Path to course-evidence.json.",
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
                files, key=lambda path: path.parent.name
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

        pages = {}
        for page in evidence.get("pages", []):
            requested = page.get("requested_url")
            if not requested:
                continue

            key = requested.rstrip("/")
            if key in pages:
                raise CommandError(
                    f"Duplicate requested-page evidence: {key}"
                )
            pages[key] = page

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        output = logs / f"corrected-ug-import-{stamp}.json"

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
                suffix, slug, subject, old_award, new_award,
                old_campus, new_campus, ucas, expected_fee
            ) in BATCH:
                course_id = f"C_UK003_{suffix}"
                url = BASE + slug

                try:
                    page = pages.get(url.rstrip("/"))

                    if page is None:
                        raise ValueError("Course evidence is missing.")

                    if (
                        page.get("source_url", "").rstrip("/")
                        != url.rstrip("/")
                    ):
                        raise ValueError(
                            "The page redirected to a different route."
                        )

                    if (
                        not page.get("sha256")
                        or page.get("title", "").strip() != subject
                    ):
                        raise ValueError(
                            "The source title or hash needs review."
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

                    # Read the first route summary, then validate it.
                    route = re.search(
                        r"Start date\s+(.*?)\s+Location\s+(.*?)"
                        r"\s+UCAS course code\s+(.*?)"
                        r"\s+Duration\s+(\d+)\s+years\b",
                        body,
                        flags=re.IGNORECASE,
                    )

                    if route is None:
                        raise ValueError(
                            "The primary route summary is missing."
                        )

                    if (
                        "September 2027" not in route.group(1)
                        or route.group(4) != "3"
                        or route.group(3).strip().upper() != ucas
                    ):
                        raise ValueError(
                            "The standard three-year September "
                            "2027 UCAS route differs."
                        )

                    locations = route.group(2).strip()

                    if new_campus not in locations.split():
                        raise ValueError(
                            f"Published campus does not include "
                            f"{new_campus}."
                        )

                    # Multi-campus routes in this batch explicitly
                    # publish September 2027 without a location caveat.
                    if (
                        len(locations.split()) > 1
                        and re.search(
                            r"start dates?\s+(?:may\s+)?vary",
                            body[:route.end() + 250],
                            flags=re.IGNORECASE,
                        )
                    ):
                        raise ValueError(
                            "Campus-specific intake needs confirmation."
                        )

                    if not re.match(
                        rf"\s*Undergraduate\s+"
                        rf"{re.escape(new_award)}\s+\(Hons\)\b",
                        body,
                    ):
                        # A word boundary after ')' is unsuitable
                        # when followed by whitespace: use plain prefix.
                        expected_prefix = (
                            f"Undergraduate {new_award} (Hons)"
                        )
                        if not body.strip().startswith(expected_prefix):
                            raise ValueError(
                                "Published honours award differs."
                            )

                    fee_excerpt = standard_fee_evidence(body)

                    amounts = {
                        Decimal(value.replace(",", ""))
                        for value in re.findall(
                            r"£\s*([\d,]+(?:\.\d{2})?)"
                            r"\s+International students starting"
                            r"\s+2027/28"
                            r"\s+\(full-time,\s*per year\)",
                            fee_excerpt,
                            flags=re.IGNORECASE,
                        )
                    }

                    amount = Decimal(expected_fee)

                    if amounts != {amount}:
                        raise ValueError(
                            "The standard-route international "
                            f"fee differs or is ambiguous: "
                            f"{sorted(amounts)}."
                        )

                    course = (
                        Course.objects.select_for_update()
                        .get(
                            course_id=course_id,
                            university_id="UK003",
                        )
                    )

                    old_title = f"{old_award} (Hons) {subject}"
                    new_title = f"{new_award} (Hons) {subject}"

                    if (
                        course.title not in {old_title, new_title}
                        or course.level != "UG"
                        or course.duration.strip().casefold()
                        != "3 years"
                        or not course.is_active
                        or course.work_placement
                        or course.location not in {
                            f"{old_campus} Campus",
                            f"{new_campus} Campus",
                        }
                    ):
                        raise ValueError(
                            "Stored title, campus, duration or "
                            "placement status needs review."
                        )

                    rows = list(
                        Tuition.objects.select_for_update().filter(
                            course=course,
                            residency="INTL",
                        )
                    )

                    if (
                        len(rows) != 1
                        or rows[0].currency != "GBP"
                    ):
                        raise ValueError(
                            "Exactly one GBP international "
                            "tuition record is required."
                        )

                    tuition = rows[0]
                    source = (
                        TuitionSource.objects.select_for_update()
                        .filter(tuition=tuition)
                        .first()
                    )

                    before = {
                        "course": snapshot(course, COURSE_FIELDS),
                        "tuition_fee": format(
                            Decimal(str(tuition.tuition_fee)), ".2f"
                        ),
                        "source": snapshot(source, SOURCE_FIELDS),
                    }

                    after_course = {
                        **before["course"],
                        "title": new_title,
                        "location": f"{new_campus} Campus",
                        "start_month": "September",
                        "start_year": 2027,
                        "course_url": url,
                        "ucas_cao_code": ucas,
                    }

                    corrections = {
                        key: {
                            "before": before["course"][key],
                            "after": after_course[key],
                        }
                        for key in ("title", "location")
                        if before["course"][key] != after_course[key]
                    }

                    raw = {
                        "verified": True,
                        "academic_year": YEAR,
                        "source_fee_category": "International",
                        "course_kind": "undergraduate_taught",
                        "study_year": 1,
                        "study_mode": "full_time",
                        "annual_tuition": str(amount),
                        "currency": "GBP",
                        "campus": new_campus,
                        "source_course_code": ucas,
                        "course_source_url": url,
                        "source_sha256": page["sha256"],
                        "source_checked_at": page.get("checked_at"),
                        "fee_method": (
                            "reviewed_standard_route_fee"
                        ),
                        "standard_route_fee_evidence": fee_excerpt,
                        "scholarship_status": (
                            "pending_scope_extension"
                        ),
                        "deposit_status": (
                            "pending_scope_extension"
                        ),
                        "fee_note": (
                            f"Published annual international fee "
                            f"for the standard three-year "
                            f"{new_award} (Hons) route at "
                            f"{new_campus}, September 2027. "
                            "Foundation, accelerated and placement "
                            "routes are excluded."
                        ),
                        "fee_categories": [{
                            "source_fee_category": "International",
                            "academic_year": YEAR,
                            "currency": "GBP",
                            "annual_tuition": str(amount),
                            "amount_requires_review": False,
                        }],
                    }

                    # Keep original correction history on repeat runs.
                    existing_raw = (
                        source.raw_fees
                        if source and isinstance(source.raw_fees, dict)
                        else {}
                    )
                    history = copy.deepcopy(
                        existing_raw.get(
                            "reviewed_course_corrections", {}
                        )
                    )
                    for key, value in corrections.items():
                        history.setdefault(key, value)

                    if history:
                        raw["reviewed_course_corrections"] = history

                    after = {
                        "course": after_course,
                        "tuition_fee": str(amount),
                        "source": {
                            "academic_year": YEAR,
                            "source_url": url,
                            "source_course_code": ucas,
                            "source_course_name": subject,
                            "fee_basis": "annual_tuition",
                            "raw_fees": raw,
                        },
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

                    for key, value in after["course"].items():
                        if getattr(course, key) != value:
                            setattr(course, key, value)
                            changed_fields.append(key)

                    if changed_fields:
                        course.save(update_fields=[
                            *changed_fields, "updated_at"
                        ])

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
                    row["changed"] for row in report["records"]
                )

        save_report()

        for record in report["records"]:
            self.stdout.write(
                f"{record['course_id']}: GBP "
                f"{record['before']['tuition_fee']} -> "
                f"{record['after']['tuition_fee']} — "
                f"{'update' if record['changed'] else 'unchanged'}"
            )

            for field in ("title", "location"):
                old = record["before"]["course"][field]
                new = record["after"]["course"][field]
                if old != new:
                    self.stdout.write(
                        f"  {field}: {old} -> {new}"
                    )

        self.stdout.write(
            f"\nMatched records: {len(report['records'])}"
        )
        self.stdout.write(
            "Proposed/updated records: "
            f"{sum(r['changed'] for r in report['records'])}"
        )
        self.stdout.write(
            f"Blocked: {len(report['blocked'])}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Reviewed course corrections and tuition sources saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Scholarship and deposit scope extensions are pending. "
            "Stored discounts and deposits unchanged."
        )