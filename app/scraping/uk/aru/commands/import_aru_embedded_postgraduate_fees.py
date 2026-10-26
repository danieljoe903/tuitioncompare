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

# ID, slug, stored title, published title, source heading,
# embedded duration, stored/new duration, campus, annual fee.
BATCH = [
    (
        "23", "education",
        "MA Education", "MA Education", "MA Education",
        "1 year full-time", "1 Year", "1 Year",
        "Chelmsford", "18825.00",
    ),
    (
        "57", "music-therapy",
        "MA Music Therapy", "MA Music Therapy", "MA Music Therapy",
        "24 months full-time", "2 Years", "2 Years",
        "Cambridge", "19750.00",
    ),
    (
        "58", "dramatherapy",
        "MA Dramatherapy", "MA Dramatherapy", "MA Dramatherapy",
        "24 months full-time", "2 Years", "2 Years",
        "Cambridge", "19750.00",
    ),
    (
        "60", "english-literature",
        "MA English Literature", "MA English Literature",
        "MA English Literature",
        "12 months full-time", "1 Year", "1 Year",
        "Cambridge", "19750.00",
    ),
    (
        "98", "applied-linguistics-and-tesol",
        "MA Applied Linguistics and TESOL",
        "MA Applied Linguistics and TESOL",
        "MA Applied Linguistics and TESOL",
        "1 year full-time", "1 Year", "1 Year",
        "Cambridge", "19750.00",
    ),
    (
        "101", "intercultural-communication",
        "MA Intercultural Communication",
        "MA Intercultural Communication",
        "MA Intercultural Communication",
        "1 year full-time", "1 Year", "1 Year",
        "Cambridge", "19750.00",
    ),
    (
        "97", "art-health-and-wellbeing",
        "MA Art, Health and Wellbeing",
        "MA Art, Health and Wellbeing",
        "MA, PG Cert, PG Dip Art, Health and Wellbeing",
        "1 year full-time", "1 Year", "1 Year",
        "Cambridge", "21975.00",
    ),
    (
        "73", "computer-games-development-computing",
        "MSc Computer Games Development (Programming)",
        "MSc Games Development (Programming)",
        "MSc Games Development (Programming)",
        "12 months full-time", "1 Year", "1 Year",
        "Cambridge", "21975.00",
    ),
    (
        "74", "computer-games-development-art",
        "MA Computer Games Development (Art)",
        "MA Games Development (Art)",
        "MA Games Development (Art)",
        "12 months full-time", "1 Year", "1 Year",
        "Cambridge", "21975.00",
    ),
    (
        "102", "international-social-welfare-and-social-policy",
        "MSc International Social Welfare and Social Policy",
        "MSc International Social Welfare and Social Policy",
        "MSc International Social Welfare and Social Policy",
        "13 months full-time", "1 Year", "13 Months",
        "Cambridge", "21975.00",
    ),
]

COURSE_FIELDS = (
    "title", "level", "duration", "location",
    "start_month", "start_year", "course_url",
)

SOURCE_FIELDS = (
    "academic_year", "source_url", "source_course_code",
    "source_course_name", "fee_basis", "raw_fees",
)


def snapshot(obj, fields):
    if obj is None:
        return None
    return {
        name: copy.deepcopy(getattr(obj, name))
        for name in fields
    }


def extract_configuration(page):
    """Parse the JSON assignment without executing JavaScript."""
    configurations = []

    for script in page.get("embedded_scripts", []):
        text = script.get("text", "")
        match = re.search(
            r"\b(?:var|let|const)\s+configOptions\s*=\s*",
            text,
        )
        if not match:
            continue

        try:
            value, _ = json.JSONDecoder().raw_decode(
                text[match.end():].lstrip()
            )
        except ValueError as exc:
            raise ValueError(
                "Cannot parse the embedded course configuration."
            ) from exc

        if not isinstance(value, dict):
            raise ValueError("Course configuration is not an object.")

        configurations.append(value)

    if len(configurations) != 1:
        raise ValueError(
            "Exactly one embedded course configuration is required."
        )

    return configurations[0]


class Command(BaseCommand):
    help = (
        "Preview or apply 10 reviewed ARU postgraduate annual "
        "fees from embedded course configuration."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--report",
            help="Path to remaining-evidence.json.",
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
                logs.glob(
                    "remaining-evidence-*/remaining-evidence.json"
                )
            )
            if not files:
                raise CommandError(
                    "Run inspect_aru_remaining_evidence first."
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
                f"Cannot read evidence: {exc}"
            ) from exc

        if evidence.get("university_id") != "UK003":
            raise CommandError("Evidence is not for UK003.")

        pages = {}
        for page in evidence.get("pages", []):
            requested = page.get("requested_url", "").rstrip("/")
            if requested:
                if requested in pages:
                    raise CommandError(
                        f"Duplicate page evidence: {requested}"
                    )
                pages[requested] = page

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        output = logs / f"embedded-pg-import-{stamp}.json"

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
                suffix, slug, old_title, new_title, heading,
                route_duration, old_duration, new_duration,
                campus, expected_fee
            ) in BATCH:
                course_id = f"C_UK003_{suffix}"
                url = BASE + slug

                try:
                    page = pages.get(url.rstrip("/"))

                    if (
                        page is None
                        or page.get("source_url", "").rstrip("/")
                        != url.rstrip("/")
                        or page.get("title", "").strip() != heading
                        or not page.get("sha256")
                    ):
                        raise ValueError(
                            "Page identity or source evidence differs."
                        )

                    if course_id not in page.get(
                        "candidate_course_ids", []
                    ):
                        raise ValueError(
                            "This page is not a recorded candidate "
                            "for the course."
                        )

                    configuration = extract_configuration(page)

                    options_found = [
                        option
                        for option in configuration.get(
                            "CourseOptions", []
                        )
                        if option.get("Duration", {}).get("Name")
                        == route_duration
                        and "September 2027" in [
                            item.get("Name")
                            for item in option.get("StartDate", [])
                        ]
                        and campus in [
                            item.get("ShortName")
                            for item in option.get("Location", [])
                        ]
                    ]

                    if len(options_found) != 1:
                        raise ValueError(
                            "A unique matching September 2027 "
                            "campus course option is required."
                        )

                    selected = options_found[0]
                    expected_description = (
                        "International students starting "
                        "2027/28 (full-time, per year)"
                    )

                    matching_fees = [
                        fee
                        for fee in selected.get("TuitionFees", [])
                        if fee.get("FeeBand", {}).get("Description")
                        == expected_description
                    ]

                    if len(matching_fees) != 1:
                        raise ValueError(
                            "A unique international annual fee "
                            "is required for the selected option."
                        )

                    fee_record = matching_fees[0]
                    amount = Decimal(
                        str(fee_record["FeeBand"]["Value"])
                    ).quantize(Decimal("0.01"))

                    if amount != Decimal(expected_fee):
                        raise ValueError(
                            f"Published annual fee differs: {amount}."
                        )

                    course = (
                        Course.objects.select_for_update()
                        .get(
                            course_id=course_id,
                            university_id="UK003",
                        )
                    )

                    if (
                        course.title not in {old_title, new_title}
                        or course.duration not in {
                            old_duration, new_duration
                        }
                        or course.level not in {"PG", "PGT"}
                        or course.location != f"{campus} Campus"
                        or not course.is_active
                    ):
                        raise ValueError(
                            "Stored course identity, campus "
                            "or duration needs review."
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
                        "duration": new_duration,
                        "start_month": "September",
                        "start_year": 2027,
                        "course_url": url,
                    }

                    existing_raw = (
                        source.raw_fees
                        if source and isinstance(source.raw_fees, dict)
                        else {}
                    )
                    corrections = copy.deepcopy(
                        existing_raw.get(
                            "reviewed_course_corrections", {}
                        )
                    )

                    for field in ("title", "duration"):
                        old = before["course"][field]
                        new = after_course[field]
                        if old != new:
                            corrections.setdefault(field, {
                                "before": old,
                                "after": new,
                            })

                    raw = {
                        "verified": True,
                        "academic_year": YEAR,
                        "source_fee_category": "International",
                        "course_kind": "postgraduate_taught",
                        "study_year": 1,
                        "study_mode": "full_time",
                        "annual_tuition": str(amount),
                        "currency": "GBP",
                        "campus": campus,
                        "course_source_url": url,
                        "source_sha256": page["sha256"],
                        "fee_method": (
                            "reviewed_embedded_course_option"
                        ),
                        "selected_option": {
                            key: copy.deepcopy(selected.get(key))
                            for key in (
                                "Duration", "StartDate", "Location"
                            )
                        },
                        "selected_fee": copy.deepcopy(fee_record),
                        "scholarship_status": (
                            "pending_separate_review"
                        ),
                        "deposit_status": (
                            "pending_scope_extension"
                        ),
                        "fee_note": (
                            f"Published annual international fee "
                            f"for September 2027, {campus}, "
                            f"{route_duration}. This is the "
                            "annual amount, not an inferred "
                            "whole-programme total."
                        ),
                        "fee_categories": [{
                            "source_fee_category": "International",
                            "academic_year": YEAR,
                            "currency": "GBP",
                            "annual_tuition": str(amount),
                            "amount_requires_review": False,
                        }],
                    }

                    if corrections:
                        raw["reviewed_course_corrections"] = corrections

                    after = {
                        "course": after_course,
                        "tuition_fee": str(amount),
                        "source": {
                            "academic_year": YEAR,
                            "source_url": url,
                            "source_course_code": "",
                            "source_course_name": new_title,
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

                except (
                    ValueError,
                    KeyError,
                    Course.DoesNotExist,
                ) as exc:
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

            for field in ("title", "duration"):
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
            "Reviewed course corrections and annual fees saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Scholarship and deposit scope reconciliation is pending. "
            "Stored discounts and deposits unchanged."
        )