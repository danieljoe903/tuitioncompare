import json
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

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

from app.scraping.uk.aberystwyth.commands.import_aberystwyth_fees import FEE_URL, money, read_json, snapshot, validate_age


YEAR = "2027/28"

REVIEWED = {
    "C_ABE002_RES_01": {
        "title": "Biosciences",
        "code": "C190",
        "award": "MRES",
        "level": "MRES",
        "path": "/postgraduate/biosciences-masters-research",
        "fee_group": "science_masters",
        "band": "Sciences",
        "course_kind": "postgraduate_mres",
    },
    "C_UK002_46": {
        "title": "Project Management",
        "code": "N1839",
        "award": "MBA",
        "level": "PG",
        "path": "/postgraduate/project-management",
        "fee_group": "mba",
        "band": "MBA",
        "course_kind": "postgraduate_taught",
    },
    "C_UK002_64": {
        "title": "International Marketing",
        "code": "N1840",
        "award": "MBA",
        "level": "PG",
        "path": "/postgraduate/international-marketing",
        "fee_group": "mba",
        "band": "MBA",
        "course_kind": "postgraduate_taught",
    },
}


def extract_prices(report):
    policy = report.get("policies", {}).get("international_fees", {})

    if (
        policy.get("status") != "collected"
        or policy.get("source_url") != FEE_URL
        or "2027/2028" not in policy.get("text", "")
    ):
        raise CommandError("Collected 2027/28 tuition policy is missing.")

    matches = []

    for rows in policy.get("tables", []):
        if not rows or len(rows[0]) < 4:
            continue

        headers = [
            str(cell).strip().casefold()
            for cell in rows[0]
        ]

        if (
            headers[1:3] != ["humanities", "sciences"]
            or "business administration" not in headers[3]
        ):
            continue

        masters = [
            row for row in rows[1:]
            if row and str(row[0]).startswith("Masters Programme")
        ]

        if len(masters) != 1 or len(masters[0]) < 4:
            continue

        row = masters[0]

        if "MRes" not in row[0]:
            continue

        prices = {
            "science_masters": money(row[2]),
            "mba": money(row[3]),
        }

        if prices == {
            "science_masters": Decimal("23820.00"),
            "mba": Decimal("23310.00"),
        }:
            matches.append(prices)

    if len(matches) != 1:
        raise CommandError(
            "Reviewed Master's/MRes/MBA prices changed or are ambiguous."
        )

    return matches[0]


def select_evidence(record, reviewed):
    matches = []

    for candidate in record.get("candidates", []):
        url = candidate.get("source_url", "")
        parsed = urlsplit(url)
        evidence = candidate.get("evidence", {})

        if (
            parsed.scheme == "https"
            and parsed.netloc == "courses.aber.ac.uk"
            and parsed.path.rstrip("/") == reviewed["path"]
            and not parsed.query
            and not parsed.fragment
            and evidence.get("status") == "collected"
        ):
            matches.append((url, evidence))

    if len(matches) != 1:
        raise ValueError("A unique collected official course page is required.")

    url, evidence = matches[0]

    if evidence.get("title") != reviewed["title"]:
        raise ValueError("Official course title changed.")

    content = evidence.get("text", "")

    if not re.search(
        rf"\b{re.escape(reviewed['code'])}-"
        rf"{re.escape(reviewed['award'])}\b",
        content,
        flags=re.IGNORECASE,
    ):
        raise ValueError("Official course code or qualification changed.")

    if not re.search(
        r"Course duration\s+1\s+year\b",
        content,
        flags=re.IGNORECASE,
    ):
        raise ValueError("The reviewed one-year duration changed.")

    if evidence.get("advertised_intakes") != [
        {"month": "September", "year": 2027}
    ]:
        raise ValueError("Reviewed September 2027 intake changed.")

    return url, evidence


class Command(BaseCommand):
    help = (
        "Preview/apply reviewed fees from the complete remaining-course "
        "report. Unconfirmed candidates remain untouched."
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
            raise CommandError("No remaining-course-review.json found.")

        evidence_path = max(paths, key=lambda path: path.parent.name)
        report = read_json(evidence_path)

        if report.get("university_id") != "UK002":
            raise CommandError("University evidence mismatch.")

        validate_age(report)
        prices = extract_prices(report)

        records = {}
        for record in report.get("records", []):
            course_id = record.get("course_id")
            if not course_id or course_id in records:
                raise CommandError("Missing or duplicate evidence course ID.")
            records[course_id] = record

        proposals = []
        blocked = []
        locked = {}
        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        output = directory / f"remaining-import-{stamp}.json"

        with transaction.atomic():
            try:
                university = University.objects.select_for_update().get(
                    pk="UK002",
                    country="UK",
                )
            except University.DoesNotExist as exc:
                raise CommandError("Aberystwyth UK002 not found.") from exc

            if university.name.strip().casefold() != "aberystwyth university":
                raise CommandError("University identity mismatch.")

            for course_id, reviewed in REVIEWED.items():
                record = records.get(course_id)

                try:
                    if record is None:
                        raise ValueError("Course is missing from the report.")
                    url, page = select_evidence(record, reviewed)
                except ValueError as exc:
                    blocked.append({
                        "course_id": course_id,
                        "reason": str(exc),
                    })
                    continue

                course = Course.objects.select_for_update().filter(
                    pk=course_id,
                    university=university,
                    is_active=True,
                ).first()

                stored = record.get("stored", {})

                if (
                    course is None
                    or course.title != stored.get("title")
                    or course.level != reviewed["level"]
                    or course.duration != stored.get("duration")
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Database course identity changed.",
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

                before = snapshot(course, tuition, source)
                before["tuition"]["tuition_fee"] = str(
                    Decimal(str(tuition.tuition_fee)).quantize(
                        Decimal("0.01")
                    )
                )

                fee = prices[reviewed["fee_group"]]
                annotation = (
                    "Published base tuition for the first study year. "
                    "Project-specific supplementary costs, where applicable, "
                    "are excluded and require university confirmation."
                )

                raw = {
                    "verified": True,
                    "academic_year": YEAR,
                    "source_fee_category": "International",
                    "fee_band": reviewed["band"],
                    "programme_fee_group": "masters",
                    "course_kind": reviewed["course_kind"],
                    "course_source_url": url,
                    "annual_tuition": str(fee),
                    "currency": "GBP",
                    "study_year": 1,
                    "fee_method": "reviewed_course_and_published_band",
                    "scholarship_status": "pending_separate_reconciliation",
                    "deposit_status": "offer_letter_amount_required",
                    "supplementary_charges_included": False,
                    "fee_categories": [{
                        "source_fee_category": "International",
                        "academic_year": YEAR,
                        "currency": "GBP",
                        "annual_tuition": str(fee),
                        "raw_annual_fee": f"£{fee:,.2f}",
                        "footnote_marker": "",
                        "annotation": annotation,
                        "amount_requires_review": False,
                    }],
                }

                after = {
                    "course": {
                        **before["course"],
                        "start_month": "September",
                        "start_year": 2027,
                        "course_url": url,
                    },
                    "tuition": {
                        "tuition_fee": str(fee),
                        "currency": "GBP",
                    },
                    "source": {
                        "academic_year": YEAR,
                        "source_url": FEE_URL,
                        "source_course_name": reviewed["title"],
                        "source_course_code": reviewed["code"],
                        "fee_basis": "annual_tuition",
                        "raw_fees": raw,
                    },
                }

                changed = before != after

                proposals.append({
                    "course_id": course_id,
                    "changed": changed,
                    "before": before,
                    "after": after,
                })
                locked[course_id] = (course, tuition)

                self.stdout.write(
                    f"{course_id}: GBP {tuition.tuition_fee} -> {fee}; "
                    f"September 2027 — "
                    f"{'update' if changed else 'unchanged'}"
                )

            held = [
                {
                    "course_id": course_id,
                    "title": record.get("stored", {}).get("title"),
                    "reason": (
                        "Candidate identity, applicable fee category or "
                        "special-route charges require further verification."
                    ),
                }
                for course_id, record in records.items()
                if course_id not in REVIEWED
            ]

            output.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "university_id": "UK002",
                        "mode": "apply" if options["apply"] else "preview",
                        "evidence_report": str(evidence_path),
                        "proposals": proposals,
                        "blocked": blocked,
                        "held": held,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            if options["apply"] and blocked:
                raise CommandError(
                    f"{len(blocked)} reviewed records are blocked. "
                    f"No changes applied. See {output}"
                )

            if options["apply"]:
                for proposal in proposals:
                    if not proposal["changed"]:
                        continue

                    course, tuition = locked[proposal["course_id"]]
                    after = proposal["after"]

                    for field in ("start_month", "start_year", "course_url"):
                        setattr(course, field, after["course"][field])

                    course.save(update_fields=[
                        "start_month",
                        "start_year",
                        "course_url",
                        "updated_at",
                    ])

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
                        course_identifier=course.pk,
                        residency="INTL",
                        before=proposal["before"],
                        after=after,
                    )

        self.stdout.write(f"\nReviewed matches: {len(proposals)}")
        self.stdout.write(
            f"Proposed changes: {sum(p['changed'] for p in proposals)}"
        )
        self.stdout.write(f"Blocked reviewed records: {len(blocked)}")
        self.stdout.write(f"Other records still under review: {len(held)}")
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Applied reviewed tuition, intake and source updates."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Scholarships, stored deposits and EU tuition unchanged."
        )