import copy
import json
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
    University,
)

from app.scraping.uk.aberystwyth.commands.correct_aberystwyth_remaining_courses import MATCHES
from app.scraping.uk.aberystwyth.commands.import_aberystwyth_fees import FEE_URL, money, read_json, snapshot, validate_age


# Course ID: academic year, programme fee group, subject band,
# expected reviewed fee, course kind.
FEES = {
    "C_ABE002_RES_02": (
        "2027/28", "masters", "Sciences", "23820.00",
        "postgraduate_mres",
    ),
    "C_ABE002_RES_04": (
        "2027/28", "masters", "Humanities", "22115.00",
        "postgraduate_taught",
    ),
    "C_ABE002_RES_06": (
        "2026/27", "research", "Sciences", "21515.00",
        "postgraduate_research",
    ),
    "C_UK002_10": (
        "2027/28", "bachelors", "Humanities", "20400.00",
        "undergraduate",
    ),
    "C_UK002_41": (
        "2027/28", "bachelors", "Humanities", "20400.00",
        "undergraduate",
    ),
    "C_UK002_60": (
        "2027/28", "bachelors", "Humanities", "20400.00",
        "undergraduate",
    ),
    "C_UK002_84": (
        "2027/28", "bachelors", "Sciences", "23255.00",
        "undergraduate",
    ),
}


def extract_prices(report):
    policy = report.get("policies", {}).get("international_fees", {})

    if (
        policy.get("status") != "collected"
        or policy.get("source_url", "").rstrip("/")
        != FEE_URL.rstrip("/")
    ):
        raise CommandError("Collected international fee policy is missing.")

    content = policy.get("text", "")

    for required in ("2026/2027", "2027/2028", "January 2027"):
        if required not in content:
            raise CommandError(
                f"Reviewed academic-year evidence missing: {required}"
            )

    # Identify each previously reviewed table using its published
    # undergraduate humanities fee, then validate all required amounts.
    table_anchors = {
        "2026/27": Decimal("19190.00"),
        "2027/28": Decimal("20400.00"),
    }

    selected_tables = {}

    for rows in policy.get("tables", []):
        if not rows or len(rows[0]) < 3:
            continue

        headers = [
            str(cell).strip().casefold()
            for cell in rows[0]
        ]

        if headers[1:3] != ["humanities", "sciences"]:
            continue

        bachelors = [
            row for row in rows[1:]
            if row and str(row[0]).startswith("Bachelors Programme")
        ]

        if len(bachelors) != 1 or len(bachelors[0]) < 3:
            continue

        anchor = money(bachelors[0][1])

        for year, expected in table_anchors.items():
            if anchor == expected:
                if year in selected_tables:
                    raise CommandError(
                        f"Multiple matching fee tables for {year}."
                    )
                selected_tables[year] = rows

    if set(selected_tables) != set(table_anchors):
        raise CommandError("Cannot identify both reviewed fee-year tables.")

    prefixes = {
        "bachelors": "Bachelors Programme",
        "masters": "Masters Programme",
        "research": "Research Programme",
    }
    columns = {"Humanities": 1, "Sciences": 2}
    prices = {}

    for year, group, band, expected, _kind in FEES.values():
        key = (year, group, band)

        if key in prices:
            continue

        rows = [
            row
            for row in selected_tables[year][1:]
            if row and str(row[0]).startswith(prefixes[group])
        ]

        if len(rows) != 1 or len(rows[0]) <= columns[band]:
            raise CommandError(f"Ambiguous published fee: {key}")

        fee = money(rows[0][columns[band]])

        if fee != Decimal(expected):
            raise CommandError(
                f"{key}: published fee changed to {fee}; review required."
            )

        if group == "masters" and "MRes" not in rows[0][0]:
            raise CommandError(
                "The reviewed Master's fee row no longer includes MRes."
            )

        prices[key] = fee

    return prices


class Command(BaseCommand):
    help = (
        "Preview/apply international tuition for seven accepted "
        "Aberystwyth course corrections."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        directory = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
        paths = list(
            directory.glob(
                "remaining-review-*/remaining-course-review.json"
            )
        )

        if not paths:
            raise CommandError("Remaining-course evidence report not found.")

        evidence_path = max(paths, key=lambda path: path.parent.name)
        evidence = read_json(evidence_path)

        if evidence.get("university_id") != "UK002":
            raise CommandError("University evidence mismatch.")

        validate_age(evidence)
        prices = extract_prices(evidence)

        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        output = directory / f"corrected-fees-{stamp}.json"

        proposals = []
        blocked = []
        locked = {}

        with transaction.atomic():
            university = University.objects.select_for_update().get(
                pk="UK002",
                country="UK",
            )

            if university.name.strip().casefold() != "aberystwyth university":
                raise CommandError("University identity mismatch.")

            for course_id, fee_details in FEES.items():
                year, group, band, expected, kind = fee_details

                (
                    official_title,
                    code,
                    award,
                    db_title,
                    level,
                    duration,
                    month,
                    intake_year,
                    path,
                ) = MATCHES[course_id]

                course = Course.objects.select_for_update().filter(
                    pk=course_id,
                    university=university,
                    is_active=True,
                ).first()

                expected_url = f"https://courses.aber.ac.uk{path}/"

                if (
                    course is None
                    or course.title != db_title
                    or course.level != level
                    or course.duration != duration
                    or course.start_month != month
                    or course.start_year != intake_year
                    or course.course_url.rstrip("/")
                    != expected_url.rstrip("/")
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": (
                            "Accepted course corrections are missing "
                            "or the database identity changed."
                        ),
                    })
                    continue

                tuitions = list(
                    Tuition.objects.select_for_update().filter(
                        course=course,
                        residency="INTL",
                    )
                )

                if len(tuitions) != 1 or tuitions[0].currency != "GBP":
                    blocked.append({
                        "course_id": course_id,
                        "reason": "A unique GBP international tuition is required.",
                    })
                    continue

                tuition = tuitions[0]
                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                raw_before = source.raw_fees if source else None
                identity = (
                    raw_before.get("course_identity", {})
                    if isinstance(raw_before, dict) else {}
                )

                if (
                    identity.get("status")
                    != "accepted_current_catalogue_match"
                    or identity.get("official_code") != code
                    or identity.get("official_award") != award
                    or identity.get("source_url", "").rstrip("/")
                    != expected_url.rstrip("/")
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Saved accepted mapping evidence is missing.",
                    })
                    continue

                fee = prices[(year, group, band)]
                before = snapshot(course, tuition, source)
                before["tuition"]["tuition_fee"] = str(
                    Decimal(str(tuition.tuition_fee)).quantize(
                        Decimal("0.01")
                    )
                )

                annotation = "Published base tuition for the first study year."

                if group == "research":
                    annotation += (
                        " Research fees may increase annually; "
                        "project-specific supplementary costs are excluded."
                    )
                elif kind == "postgraduate_mres":
                    annotation += (
                        " Project-specific supplementary costs, if any, "
                        "require university confirmation."
                    )

                raw_after = copy.deepcopy(raw_before)

                # Remove the old pending-fee display message now that
                # this specific fee has been verified.
                notes = raw_after.get("display_notes")
                if isinstance(notes, dict):
                    notes.pop("fee", None)

                raw_after.update({
                    "verified": True,
                    "fee_verification_status": "verified",
                    "academic_year": year,
                    "source_fee_category": "International",
                    "fee_band": band,
                    "programme_fee_group": group,
                    "course_kind": kind,
                    "course_source_url": expected_url,
                    "annual_tuition": str(fee),
                    "currency": "GBP",
                    "study_year": 1,
                    "fee_method": "reviewed_course_and_published_band",
                    "supplementary_charges_included": False,
                    "fee_review": {
                        "status": "resolved",
                        "reason": (
                            "Accepted course identity matched to its "
                            "published intake-year tuition category."
                        ),
                    },
                    "scholarship_status": "pending_separate_reconciliation",
                    "deposit_status": "offer_letter_amount_required",
                    "fee_categories": [{
                        "source_fee_category": "International",
                        "academic_year": year,
                        "currency": "GBP",
                        "annual_tuition": str(fee),
                        "raw_annual_fee": f"£{fee:,.2f}",
                        "annotation": annotation,
                        "footnote_marker": "",
                        "amount_requires_review": False,
                    }],
                })

                after = {
                    "course": before["course"],
                    "tuition": {
                        "tuition_fee": str(fee),
                        "currency": "GBP",
                    },
                    "source": {
                        "academic_year": year,
                        "source_url": FEE_URL,
                        "source_course_name": official_title,
                        "source_course_code": code,
                        "fee_basis": "annual_tuition",
                        "raw_fees": raw_after,
                    },
                }

                changed = before != after
                proposals.append({
                    "course_id": course_id,
                    "changed": changed,
                    "before": before,
                    "after": after,
                })
                locked[course_id] = tuition

                self.stdout.write(
                    f"{course_id}: GBP {tuition.tuition_fee} -> {fee}; "
                    f"{year} — {'update' if changed else 'unchanged'}"
                )

            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "university_id": "UK002",
                        "mode": "apply" if options["apply"] else "preview",
                        "evidence_report": str(evidence_path),
                        "proposals": proposals,
                        "blocked": blocked,
                        "scholarship_amounts_changed": False,
                        "deposit_amounts_changed": False,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            if options["apply"] and blocked:
                raise CommandError(
                    f"{len(blocked)} records require review. "
                    f"No batch changes applied. See {output}"
                )

            if options["apply"]:
                for proposal in proposals:
                    if not proposal["changed"]:
                        continue

                    tuition = locked[proposal["course_id"]]
                    after = proposal["after"]

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
                        course_identifier=proposal["course_id"],
                        residency="INTL",
                        before=proposal["before"],
                        after=after,
                    )

        self.stdout.write(f"\nMatched records: {len(proposals)}")
        self.stdout.write(
            f"Proposed changes: {sum(p['changed'] for p in proposals)}"
        )
        self.stdout.write(f"Blocked: {len(blocked)}")
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Applied international tuition and source updates."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Scholarship amounts, stored deposits and EU tuition unchanged."
        )