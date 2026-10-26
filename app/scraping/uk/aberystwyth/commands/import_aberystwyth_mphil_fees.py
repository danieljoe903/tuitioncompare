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

from app.scraping.uk.aberystwyth.commands.correct_aberystwyth_mphil_routes import MATCHES
from app.scraping.uk.aberystwyth.commands.import_aberystwyth_fees import FEE_URL, money, read_json, snapshot, validate_age


YEAR = "2026/27"

FEES = {
    "C_ABE002_RES_03": ("Humanities", Decimal("19070.00")),
    "C_ABE002_RES_08": ("Sciences", Decimal("21515.00")),
    "C_UK002_99": ("Sciences", Decimal("21515.00")),
}


def extract_prices(report):
    policy = report.get("policies", {}).get("international_fees", {})

    if (
        policy.get("status") != "collected"
        or policy.get("source_url", "").rstrip("/")
        != FEE_URL.rstrip("/")
        or "2026/2027" not in policy.get("text", "")
        or "January 2027" not in policy.get("text", "")
    ):
        raise CommandError("Reviewed January 2027 fee evidence is missing.")

    matches = []

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
        research = [
            row for row in rows[1:]
            if row and str(row[0]).startswith("Research Programme")
        ]

        if (
            len(bachelors) != 1
            or len(research) != 1
            or len(bachelors[0]) < 3
            or len(research[0]) < 3
        ):
            continue

        # Previously reviewed anchor for the 2026/27 table.
        if money(bachelors[0][1]) != Decimal("19190.00"):
            continue

        prices = {
            "Humanities": money(research[0][1]),
            "Sciences": money(research[0][2]),
        }

        if prices != {
            "Humanities": Decimal("19070.00"),
            "Sciences": Decimal("21515.00"),
        }:
            raise CommandError(
                "Published 2026/27 research fees changed; review required."
            )

        matches.append(prices)

    if len(matches) != 1:
        raise CommandError(
            "Cannot uniquely identify the reviewed 2026/27 research table."
        )

    return matches[0]


class Command(BaseCommand):
    help = (
        "Preview/apply January 2027 international tuition for "
        "three accepted MPhil routes using 2026/27 research fees."
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

        proposals = []
        blocked = []
        locked = {}
        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        output = directory / f"mphil-fees-{stamp}.json"

        with transaction.atomic():
            university = University.objects.select_for_update().get(
                pk="UK002",
                country="UK",
            )

            if university.name.strip().casefold() != "aberystwyth university":
                raise CommandError("University identity mismatch.")

            for course_id, (band, expected_fee) in FEES.items():
                match = MATCHES[course_id]
                url = f"https://courses.aber.ac.uk{match['path']}/"

                course = Course.objects.select_for_update().filter(
                    pk=course_id,
                    university=university,
                    is_active=True,
                ).first()

                if (
                    course is None
                    or course.title != match["title"]
                    or course.level != "PG"
                    or course.duration != "1 Year"
                    or course.start_month != "January"
                    or course.start_year != 2027
                    or course.course_url.rstrip("/") != url.rstrip("/")
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Accepted MPhil course corrections are missing.",
                    })
                    continue

                records = list(
                    Tuition.objects.select_for_update().filter(
                        course=course,
                        residency="INTL",
                    )
                )

                if len(records) != 1 or records[0].currency != "GBP":
                    blocked.append({
                        "course_id": course_id,
                        "reason": "A unique GBP international tuition is required.",
                    })
                    continue

                tuition = records[0]
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
                    or identity.get("official_code") != match["code"]
                    or identity.get("official_award") != "MPHL"
                    or identity.get("source_url", "").rstrip("/")
                    != url.rstrip("/")
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Saved accepted MPhil mapping evidence missing.",
                    })
                    continue

                fee = prices[band]

                if fee != expected_fee:
                    raise CommandError("Reviewed fee validation failed.")

                before = snapshot(course, tuition, source)
                before["tuition"]["tuition_fee"] = str(
                    Decimal(str(tuition.tuition_fee)).quantize(
                        Decimal("0.01")
                    )
                )

                raw_after = copy.deepcopy(raw_before)
                notes = raw_after.get("display_notes")

                if isinstance(notes, dict):
                    notes.pop("fee", None)

                annotation = (
                    "First study-year base research tuition. "
                    "Research fees may increase annually. "
                    "Project-specific supplementary charges are excluded "
                    "and require confirmation in the university offer."
                )

                raw_after.update({
                    "verified": True,
                    "fee_verification_status": "verified",
                    "academic_year": YEAR,
                    "source_fee_category": "International",
                    "fee_band": band,
                    "programme_fee_group": "research",
                    "course_kind": "postgraduate_research",
                    "research_award": "MPHIL",
                    "course_source_url": url,
                    "annual_tuition": str(fee),
                    "currency": "GBP",
                    "study_year": 1,
                    "fee_method": "reviewed_course_and_published_band",
                    "annual_increases_possible": True,
                    "supplementary_charges_included": False,
                    "fee_review": {
                        "status": "resolved",
                        "reason": (
                            "Accepted January 2027 MPhil route matched "
                            "to the 2026/27 research fee table."
                        ),
                    },
                    "scholarship_status": "applicability_unconfirmed",
                    "deposit_status": "offer_letter_amount_required",
                    "fee_categories": [{
                        "source_fee_category": "International",
                        "academic_year": YEAR,
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
                        "academic_year": YEAR,
                        "source_url": FEE_URL,
                        "source_course_name": match["official_title"],
                        "source_course_code": match["code"],
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
                    f"January 2027 / {YEAR} — "
                    f"{'update' if changed else 'unchanged'}"
                )

            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "university_id": "UK002",
                        "academic_year": YEAR,
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
                    f"No changes applied. See {output}"
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
            "Applied MPhil international tuition and source updates."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Scholarship applicability remains pending. "
            "The saved deposit policy requires the offer-letter amount. "
            "Stored discount/deposit amounts and EU tuition are unchanged."
        )