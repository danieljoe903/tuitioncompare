import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from app.models import (
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
)


YEAR = "2026/27"

POLICY_URL = (
    "https://www.abertay.ac.uk/study-apply/"
    "money-fees-and-funding/tuition-fees-2026-2027/"
)

COURSE_BASE = (
    "https://www.abertay.ac.uk/course-search/undergraduate/"
)

# Previously reviewed mappings only.
UG_MATCHES = {
    "C_UK001_01": "accounting-and-finance",
    "C_UK001_04": "business-management-with-analytics",
    "C_UK001_05": "civil-and-environmental-engineering",
    "C_UK001_31": "accounting-and-finance-with-business-analytics",
    "C_UK001_32": "accounting-and-finance-with-law",
    "C_UK001_33": "accounting-and-finance-with-people-management",
    "C_UK001_34": "business-management-with-events-management",
    "C_UK001_35": "business-management-with-people-management",
    "C_UK001_36": "digital-marketing-and-business-management",
    "C_UK001_40": "environmental-management-sustainability",
    "C_UK001_61": "law-accelerated-graduate-entry-llb",
}

REVIEWED_ROUTE_TITLES = {
    "C_UK001_50": "MSc Civil Engineering",
    "C_UK001_88": "MSc Environment and Business",
}

PLACEMENT_YEAR_ONE_IDS = {
    "C_UK001_49",
    "C_UK001_50",
    "C_UK001_88",
}

INTAKE_REVIEW_IDS = PLACEMENT_YEAR_ONE_IDS | {
    "C_ABE001_RES_07",
}

SOURCE_FIELDS = (
    "academic_year",
    "source_url",
    "source_course_code",
    "source_course_name",
    "fee_basis",
    "student_contribution",
    "published_total",
    "raw_fees",
)


def serialisable(value):
    return json.loads(
        json.dumps(value, cls=DjangoJSONEncoder)
    )


def normalise(title):
    title = title.casefold().replace("&", " and ")
    title = re.sub(r"\bux\b", "user experience", title)
    title = " ".join(re.findall(r"[a-z0-9]+", title))

    aliases = {
        "professional masters in games development mprof":
            "mprof games development",
    }

    return aliases.get(title, title)


def research_subject(title):
    title = title.casefold().replace("&", " and ")

    title = re.sub(
        r"^postgraduate research opportunities in\s+",
        "",
        title,
    )
    title = re.sub(r"\(food and drink\)", "", title)

    title = re.sub(
        r"^(msc|ma|llm)\s+by\s+research\s+(in\s+)?",
        "",
        title,
    )
    title = re.sub(
        r"\b(msc|ma|llm)\s+by\s+research\b.*$",
        "",
        title,
    )
    title = re.sub(r"^(msc|ma|llm)\s+", "", title)
    title = re.sub(r"\s+by\s+research$", "", title)

    return " ".join(re.findall(r"[a-z0-9]+", title))


def research_award(title):
    match = re.search(
        r"\b(MSc|MA|LLM)\b",
        title,
        flags=re.IGNORECASE,
    )

    return match.group(1).casefold() if match else None


def one_year(duration):
    return bool(
        re.fullmatch(
            r"1\s+years?",
            (duration or "").strip(),
            flags=re.IGNORECASE,
        )
    )


def money(raw):
    """Read the leading GBP amount; preserve notes separately."""
    if not isinstance(raw, str):
        raise ValueError("Fee must be text.")

    match = re.match(
        r"^£\s*((?:\d{1,3}(?:,\d{3})+|\d+)"
        r"(?:\.\d{2})?)(?![\d,.])",
        raw.strip(),
    )

    if not match:
        raise ValueError(f"Unsupported fee: {raw!r}")

    amount = Decimal(match.group(1).replace(",", ""))

    if not amount.is_finite() or amount <= 0:
        raise ValueError("Invalid tuition amount.")

    return amount.quantize(Decimal("0.01"))


def section(report, heading):
    matches = [
        item
        for item in report["sections"]
        if item["heading"] == heading
    ]

    if len(matches) != 1:
        raise ValueError(f"Expected one section: {heading}")

    return matches[0]


def fee_table(section_data, first_header):
    matches = [
        table
        for table in section_data["tables"]
        if table
        and table[0]
        and table[0][0].casefold() == first_header.casefold()
    ]

    if len(matches) != 1:
        raise ValueError(
            f"Expected one table headed {first_header!r}."
        )

    return matches[0]


def taught_policy_rows(table, title):
    """Match complete named degrees, not arbitrary substrings."""
    wanted = normalise(title)
    found = []

    for row in table[1:]:
        if len(row) != 2 or not row[1].strip():
            continue

        names = re.split(
            r"(?=\b(?:MSc|MProf|Global MBA)\b)",
            row[0],
        )

        if any(normalise(name) == wanted for name in names):
            found.append(row)

    return found


def metadata_for_comparison(metadata):
    """Ignore evidence-file changes when comparing fee metadata."""
    result = serialisable(metadata)

    if result is not None:
        raw = result.get("raw_fees")

        if isinstance(raw, dict):
            raw.pop("policy_sha256", None)
            raw.pop("policy_snapshot", None)

    return result


class Command(BaseCommand):
    help = (
        "Import reviewed Abertay 2026/27 international fees. "
        "Preview unless --apply is supplied."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)

        try:
            policy_path = root / 'data/universities/uk/abertay/abertay_remaining_policy.json'
            policy_text = policy_path.read_text(
                encoding="utf-8-sig"
            )
            policy = json.loads(policy_text)

            remaining = json.loads(
                (root / 'data/universities/uk/abertay/abertay_remaining_preview.json')
                .read_text(encoding="utf-8-sig")
            )

            undergraduate = json.loads(
                (root / 'data/universities/uk/abertay/abertay_courses_preview.json')
                .read_text(encoding="utf-8-sig")
            )

            if (
                policy["source_url"] != POLICY_URL
                or policy["page_title"] != "Tuition Fees (2026-2027)"
                or remaining["academic_year"] != YEAR
                or undergraduate["academic_year"] != YEAR
            ):
                raise ValueError(
                    "Unexpected source or academic year."
                )

            ug_section = section(
                policy,
                "Undergraduate Tuition Fees - "
                "International Students 2026/27",
            )
            pg_section = section(
                policy,
                "Postgraduate Tuition Fees - "
                "International Students 2026/27",
            )

            ug_table = fee_table(
                ug_section,
                "UNDERGRADUATE DEGREE COURSES",
            )
            pg_table = fee_table(
                pg_section,
                "POSTGRADUATE COURSES",
            )

            default_rows = [
                row
                for row in ug_table[1:]
                if len(row) == 2
                and row[0] == (
                    "All other Undergraduate Bachelors "
                    "Degree Programmes"
                )
            ]

            research_rows = [
                row
                for row in pg_table[1:]
                if len(row) == 2
                and row[0] == "Masters by Research (all subjects)"
            ]

            if len(default_rows) != 1 or len(research_rows) != 1:
                raise ValueError(
                    "Central fee rules are ambiguous."
                )

            ug_fee = money(default_rows[0][1])
            research_fee = money(research_rows[0][1])

            # Review gates for this existing pilot command.
            if ug_fee != Decimal("16950.00"):
                raise ValueError(
                    "Undergraduate policy fee changed; review required."
                )

            if research_fee != Decimal("22000.00"):
                raise ValueError(
                    "Research policy fee changed; review required."
                )

            postgraduate = remaining["postgraduate"]

            if not isinstance(postgraduate, list):
                raise ValueError(
                    "Postgraduate preview must contain a list."
                )

        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            InvalidOperation,
        ) as exc:
            raise CommandError(
                f"Could not load validated source data: {exc}"
            ) from exc

        evidence_hash = hashlib.sha256(
            policy_text.encode("utf-8")
        ).hexdigest()

        ug_sources = {}

        for record in undergraduate["records"]:
            if record.get("status") == "extracted":
                ug_sources.setdefault(
                    record["source_url"], []
                ).append(record)

        plans = []
        blocked = []
        skipped = []

        def course_summary(course, reason):
            return {
                "course_id": course.course_id,
                "title": course.title,
                "start_month": course.start_month,
                "start_year": course.start_year,
                "duration": course.duration,
                "reason": reason,
            }

        def block(course, reason):
            blocked.append(course_summary(course, reason))

        with transaction.atomic():
            verified_ids = set(
                TuitionSource.objects.filter(
                    tuition__course__university__name__iexact=(
                        "Abertay University"
                    ),
                    tuition__course__university__country="UK",
                    tuition__residency="INTL",
                    academic_year=YEAR,
                    fee_basis="annual_tuition",
                ).values_list("tuition__course_id", flat=True)
            )

            courses = list(
                Course.objects.select_for_update()
                .filter(
                    university__name__iexact="Abertay University",
                    university__country="UK",
                    is_active=True,
                )
                # These records have a dedicated importer that
                # preserves their international-application notices.
                .exclude(
                    course_id__in=[
                        "C_ABE001_RES_06",
                        "C_ABE001_RES_08",
                    ]
                )
                .order_by("course_id")
            )

            for course in courses:
                intake_month = (
                    course.start_month or ""
                ).strip().casefold()

                # This importer must not overwrite early-2026
                # cohort fees with 2026/27 pricing.
                if (
                    course.start_year == 2026
                    and intake_month in {"january", "february"}
                ):
                    skipped.append(
                        course_summary(
                            course,
                            "Early-2026 intake: excluded from this "
                            "2026/27 importer. Check the separate "
                            "cohort importer and coverage report.",
                        )
                    )
                    continue

                if course.start_year != 2026:
                    block(course, "Existing intake is not 2026.")
                    continue

                data = None
                source_url = None
                kind = None
                note = None

                if course.course_id in UG_MATCHES:
                    url = (
                        COURSE_BASE
                        + UG_MATCHES[course.course_id]
                        + "/"
                    )
                    matches = ug_sources.get(url, [])

                    if len(matches) != 1:
                        block(
                            course,
                            "Expected one undergraduate source.",
                        )
                        continue

                    data = deepcopy(matches[0]["data"])

                    try:
                        source_amount = Decimal(
                            data["annual_tuition"]
                        )
                    except (
                        KeyError,
                        InvalidOperation,
                        TypeError,
                        ValueError,
                    ):
                        block(course, "Invalid undergraduate amount.")
                        continue

                    if (
                        course.level != "UG"
                        or data.get("academic_year") != YEAR
                        or data.get("currency") != "GBP"
                        or not source_amount.is_finite()
                        or source_amount != ug_fee
                        or data.get("footnote_marker")
                    ):
                        block(
                            course,
                            "Undergraduate fee requires review.",
                        )
                        continue

                    exported_candidates = [
                        candidate
                        for record in remaining[
                            "undergraduate_remaining"
                        ]
                        for candidate in record.get("candidates", [])
                        if candidate["course_id"] == course.course_id
                    ]

                    if course.course_id == "C_UK001_61":
                        title_ok = (
                            normalise(course.title)
                            == normalise(
                                "Bachelor of Laws (LLB) "
                                "Honours (Accelerated)"
                            )
                        )
                    else:
                        title_ok = any(
                            candidate["title"] == course.title
                            for candidate in exported_candidates
                        )

                    if not title_ok:
                        block(
                            course,
                            "Database title changed since review.",
                        )
                        continue

                    source_url = url
                    kind = "undergraduate"
                    fee = ug_fee
                    note = (
                        "Published annual bachelor fee corroborates "
                        "the course-page amount. Stored duration and "
                        "entry route have not been changed or verified."
                    )

                elif course.level in {"PG", "PGT", "MRES"}:
                    if "by research" in course.title.casefold():
                        award = research_award(course.title)
                        subject = research_subject(course.title)

                        matches = [
                            record
                            for record in postgraduate
                            if record.get("kind")
                            == "postgraduate_research"
                            and award is not None
                            and research_award(
                                record.get("source_title", "")
                            ) == award
                            and research_subject(
                                record.get("source_title", "")
                            ) == subject
                        ]

                        if (
                            len(matches) != 1
                            or not one_year(course.duration)
                        ):
                            block(
                                course,
                                "Research subject, award or "
                                "duration needs review.",
                            )
                            continue

                        record = matches[0]
                        source_url = record["source_url"]
                        fee = research_fee
                        kind = "masters_by_research"

                        data = {
                            "title": record["source_title"],
                            "raw_fee": research_rows[0][1],
                            "fee_categories": [
                                {
                                    "source_fee_category": "International",
                                    "academic_year": YEAR,
                                    "currency": "GBP",
                                    "raw_annual_fee": research_rows[0][1],
                                    "annual_tuition": str(fee),
                                    "footnote_marker": "",
                                    "annotation": "",
                                    "amount_requires_review": False,
                                }
                            ],
                        }

                        note = (
                            "Masters by Research fee only. "
                            "No PhD/MPhil fee or taught postgraduate "
                            "scholarship has been applied."
                        )

                    else:
                        source_title = REVIEWED_ROUTE_TITLES.get(
                            course.course_id,
                            course.title,
                        )

                        matches = [
                            record
                            for record in postgraduate
                            if record.get("kind")
                            == "postgraduate_taught"
                            and record.get("status") == "extracted"
                            and normalise(
                                record["data"]["title"]
                            ) == normalise(source_title)
                        ]

                        if len(matches) != 1:
                            block(
                                course,
                                "Taught course identity needs review.",
                            )
                            continue

                        record = matches[0]
                        data = deepcopy(record["data"])

                        reviewed_placement_year_one = (
                            course.course_id
                            in PLACEMENT_YEAR_ONE_IDS
                            and re.fullmatch(
                                r"2\s+years?",
                                (course.duration or "").strip(),
                                flags=re.IGNORECASE,
                            ) is not None
                        )

                        if (
                            not (
                                one_year(course.duration)
                                or reviewed_placement_year_one
                            )
                            or "12 months" not in (
                                data.get("duration") or ""
                            ).casefold()
                        ):
                            block(
                                course,
                                "Duration/intake or placement "
                                "fee needs review.",
                            )
                            continue

                        rows = taught_policy_rows(
                            pg_table,
                            course.title,
                        )

                        if len(rows) != 1:
                            block(
                                course,
                                "Central named fee is ambiguous.",
                            )
                            continue

                        try:
                            fee = Decimal(data["annual_tuition"])
                            central_amount = money(rows[0][1])
                        except (
                            KeyError,
                            InvalidOperation,
                            TypeError,
                            ValueError,
                        ):
                            block(
                                course,
                                "Invalid taught postgraduate amount.",
                            )
                            continue

                        if (
                            data.get("academic_year") != YEAR
                            or data.get("currency") != "GBP"
                            or not fee.is_finite()
                            or fee != central_amount
                            or data.get("footnote_marker")
                        ):
                            block(
                                course,
                                "Course and central fee "
                                "need reconciliation.",
                            )
                            continue

                        source_url = record["source_url"]
                        kind = "postgraduate_taught"

                        note = (
                            "One-year taught fee corroborated by "
                            "the central named-course table. "
                            "Additional charges may apply beyond "
                            "18 months FTE. Scholarship rules "
                            "remain separate."
                        )

                        if reviewed_placement_year_one:
                            note = (
                                "First compared study-year annual "
                                "fee for the named industrial-practice "
                                "route. Not a two-year programme total; "
                                "later-year charges require separate "
                                "verification. Stored course duration "
                                "is unchanged."
                            )

                        for category in data.get(
                            "fee_categories", []
                        ):
                            if (
                                category["source_fee_category"]
                                != "International"
                            ):
                                category["amount_requires_review"] = True
                                category["annotation"] = (
                                    "Home postgraduate fee under "
                                    "review on the central "
                                    "2026/27 policy page."
                                )

                else:
                    if course.course_id not in verified_ids:
                        block(
                            course,
                            "No reviewed undergraduate/top-up "
                            "course identity match.",
                        )
                    continue

                tuitions = list(
                    Tuition.objects.select_for_update().filter(
                        course=course,
                        residency="INTL",
                    )
                )

                if len(tuitions) != 1:
                    block(
                        course,
                        "Expected exactly one INTL tuition.",
                    )
                    continue

                tuition = tuitions[0]

                if tuition.currency != "GBP":
                    block(
                        course,
                        "Existing tuition currency is not GBP.",
                    )
                    continue

                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                if source and source.academic_year != YEAR:
                    block(
                        course,
                        "Existing metadata belongs to another year.",
                    )
                    continue

                metadata = {
                    "academic_year": YEAR,
                    "source_url": POLICY_URL,
                    "source_course_code": (
                        data.get("ucas_code") or ""
                    ),
                    "source_course_name": data["title"],
                    "fee_basis": "annual_tuition",
                    "student_contribution": None,
                    "published_total": None,
                    "raw_fees": {
                        "fee_status": "International",
                        "source_fee_category": "International",
                        "currency": "GBP",
                        "annual_tuition": data["raw_fee"],
                        "fee_categories": data.get(
                            "fee_categories", []
                        ),
                        "course_source_url": source_url,
                        "fee_method": "reviewed_central_policy",
                        "course_kind": kind,
                        "policy_sha256": evidence_hash,
                        "review_note": note,
                    },
                }

                old_metadata = (
                    {
                        field: getattr(source, field)
                        for field in SOURCE_FIELDS
                    }
                    if source else None
                )

                changed = (
                    tuition.tuition_fee != fee
                    or metadata_for_comparison(old_metadata)
                    != metadata_for_comparison(metadata)
                )

                # Preserve existing evidence metadata when only
                # the cached policy-file hash has changed.
                if not changed:
                    metadata = old_metadata

                before = serialisable({
                    "tuition_fee": tuition.tuition_fee,
                    "source_metadata": old_metadata,
                })
                after = serialisable({
                    "tuition_fee": fee,
                    "source_metadata": metadata,
                })

                if course.course_id in INTAKE_REVIEW_IDS:
                    self.stdout.write(
                        f"Review intake: {course.course_id} — "
                        f"{course.start_month} {course.start_year}; "
                        f"stored duration: {course.duration}"
                    )

                plans.append({
                    "course": course,
                    "tuition": tuition,
                    "fee": fee,
                    "metadata": metadata,
                    "kind": kind,
                    "before": before,
                    "after": after,
                    "changed": changed,
                })

            report = {
                "academic_year": YEAR,
                "mode": "apply" if options["apply"] else "preview",
                "database_changes": False,
                "committed": False,
                "proposals": [
                    {
                        "course_id": plan["course"].course_id,
                        "title": plan["course"].title,
                        "start_month": plan["course"].start_month,
                        "start_year": plan["course"].start_year,
                        "duration": plan["course"].duration,
                        "kind": plan["kind"],
                        "changed": plan["changed"],
                        "before": plan["before"],
                        "after": plan["after"],
                    }
                    for plan in plans
                ],
                "blocked": blocked,
                "skipped_other_cohorts": skipped,
                "coverage_note": (
                    "This is a 2026/27 importer report, not a "
                    "complete university coverage report. "
                    "Skipped early-2026 records may be verified "
                    "or unresolved in the separate cohort workflow."
                ),
                "undergraduate_remaining_pages": remaining[
                    "undergraduate_remaining"
                ],
                "eu_records": remaining.get("eu_records", []),
                "eu_note": (
                    "Only INTL tuition records are eligible "
                    "for updates. EU records are unchanged."
                ),
            }

            log_dir = root / "logs" / "abertay-fees"
            log_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now(timezone.utc).strftime(
                "%Y%m%dT%H%M%S%fZ"
            )
            output = log_dir / f"remaining-{timestamp}.json"

            # Write the plan before making database changes.
            output.write_text(
                json.dumps(
                    report,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            for plan in plans:
                course = plan["course"]
                tuition = plan["tuition"]
                label = "update" if plan["changed"] else "unchanged"

                self.stdout.write(
                    f"{course.course_id}: "
                    f"GBP {tuition.tuition_fee} -> "
                    f"{plan['fee']} — {label}"
                )

                if not options["apply"] or not plan["changed"]:
                    continue

                tuition.tuition_fee = plan["fee"]
                tuition.save(update_fields=["tuition_fee"])

                TuitionSource.objects.update_or_create(
                    tuition=tuition,
                    defaults=plan["metadata"],
                )

                TuitionUpdateAudit.objects.create(
                    tuition=tuition,
                    course_identifier=course.course_id,
                    residency="INTL",
                    before=plan["before"],
                    after=plan["after"],
                )

        # Database transaction has completed successfully.
        if options["apply"]:
            report["database_changes"] = any(
                plan["changed"] for plan in plans
            )
            report["committed"] = True

            output.write_text(
                json.dumps(
                    report,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        changed_count = sum(
            plan["changed"] for plan in plans
        )

        self.stdout.write(f"\nMatched records: {len(plans)}")
        self.stdout.write(
            f"Proposed/updated records: {changed_count}"
        )
        self.stdout.write(f"Blocked records: {len(blocked)}")
        self.stdout.write(
            f"Skipped early-2026 records: {len(skipped)}"
        )
        self.stdout.write(f"Before/after report: {output}")

        if options["apply"]:
            self.stdout.write(
                "Applied tuition and source metadata updates."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )