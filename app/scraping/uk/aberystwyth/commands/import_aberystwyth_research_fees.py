import json
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

from app.scraping.uk.aberystwyth.commands.import_aberystwyth_fees import FEE_URL, latest_report, money, read_json, snapshot, validate_age


YEAR = "2026/27"

# ID suffix: official subject title, award, fee band, official path.
COURSES = {
    "100": (
        "History", "PHD", "Humanities",
        "/postgraduate/research-history",
    ),
    "66": (
        "Geography (Arts)", "MPHIL", "Humanities",
        "/postgraduate/mphil-geography",
    ),
    "67": (
        "Geography and Earth Sciences (Science)", "PHD", "Sciences",
        "/postgraduate/research-iges",
    ),
    "69": (
        "Law", "PHD", "Humanities",
        "/postgraduate/M1450-law-criminology-phd",
    ),
    "70": (
        "International Politics", "PHD", "Humanities",
        "/postgraduate/research-international-politics",
    ),
    "71": (
        "Sport and Exercise Science", "MPHIL", "Sciences",
        "/postgraduate/mphil-sports-exercise",
    ),
    "72": (
        "Art", "MPHIL", "Humanities",
        "/postgraduate/mphil-art",
    ),
    "73": (
        "Agriculture", "PHD", "Sciences",
        "/postgraduate/phd-agriculture",
    ),
    "74": (
        "Criminology", "MPHIL", "Humanities",
        "/postgraduate/criminology-mphil",
    ),
    "98": (
        "Psychology", "PHD", "Sciences",
        "/postgraduate/research-psychology",
    ),
}

EXPECTED = {
    "Humanities": Decimal("19070.00"),
    "Sciences": Decimal("21515.00"),
}


def extract_prices(report):
    fees = report.get("sources", {}).get("international_fees", {})
    content = fees.get("text", "")

    if (
        fees.get("status") != "collected"
        or fees.get("response_url", "").rstrip("/")
        != FEE_URL.rstrip("/")
        or "2026/2027" not in content
        or "January 2027" not in content
    ):
        raise CommandError("Required 2026/27 fee evidence is missing.")

    matches = []

    for table in fees.get("tables", []):
        rows = table.get("rows", [])

        if not rows or len(rows[0]) < 3:
            continue

        headers = [str(value).strip().casefold() for value in rows[0]]

        if headers[1:3] != ["humanities", "sciences"]:
            continue

        selected = [
            row
            for row in rows[1:]
            if row
            and str(row[0]).startswith("Research Programmes")
        ]

        if len(selected) != 1 or len(selected[0]) < 3:
            continue

        prices = {
            "Humanities": money(selected[0][1]),
            "Sciences": money(selected[0][2]),
        }

        if prices == EXPECTED:
            matches.append(prices)

    if len(matches) != 1:
        raise CommandError(
            "Cannot uniquely identify the reviewed 2026/27 "
            "research fee table. Refresh and inspect the evidence."
        )

    return matches[0]


def validate_page(page, title, award, path):
    if not page or page.get("status") != "collected":
        return "Collected course-page evidence is missing."

    if page.get("title") != title:
        return "Official subject title changed."

    candidate = page.get("catalogue_candidate", {})

    if candidate.get("award") != award:
        return "Research award does not match the reviewed route."

    expected_duration = "3" if award == "PHD" else "1"

    if str(candidate.get("duration_years")) != expected_duration:
        return "Reviewed research duration changed."

    parsed = urlsplit(page.get("response_url", ""))

    if (
        parsed.scheme != "https"
        or parsed.netloc != "courses.aber.ac.uk"
        or parsed.path.rstrip("/") != path
        or parsed.query
        or parsed.fragment
    ):
        return "Official course URL does not match the reviewed route."

    if page.get("advertised_intakes") != [
        {"month": "January", "year": 2027}
    ]:
        return "Reviewed January 2027 intake changed."

    return None


class Command(BaseCommand):
    help = (
        "Preview/apply ten reviewed January 2027 Aberystwyth "
        "research fees using the 2026/27 fee year."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        directory = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
        source_path = latest_report(directory, "source-review.json")
        course_path = latest_report(directory, "course-evidence.json")

        source_report = read_json(source_path)
        course_report = read_json(course_path)

        for report in (source_report, course_report):
            if report.get("university_id") != "UK002":
                raise CommandError("Evidence belongs to another university.")
            validate_age(report)

        prices = extract_prices(source_report)

        pages = {}

        for page in course_report.get("pages", []):
            course_id = page.get("course_id")

            if not course_id or course_id in pages:
                raise CommandError("Missing or duplicate evidence course ID.")

            pages[course_id] = page

        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        report_path = directory / f"research-import-{stamp}.json"

        proposals = []
        blocked = []

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

            for suffix, reviewed in COURSES.items():
                course_id = f"C_UK002_{suffix}"
                title, award, band, path = reviewed
                page = pages.get(course_id)

                reason = validate_page(page, title, award, path)

                if reason:
                    blocked.append({
                        "course_id": course_id,
                        "reason": reason,
                    })
                    continue

                course = Course.objects.select_for_update().filter(
                    pk=course_id,
                    university=university,
                    is_active=True,
                ).first()

                if course is None:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Active database course not found.",
                    })
                    continue

                if (
                    course.title != page.get("stored_title")
                    or course.duration != page.get("stored_duration")
                    or course.level != page.get("stored_level")
                    or course.level not in ("PG", "MRES")
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Database identity changed since collection.",
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
                fee = prices[band]

                annotation = (
                    "First study-year base tuition, not a whole-course "
                    "total. Research fees may increase annually. "
                    "Project-specific supplementary charges are excluded "
                    "and require confirmation in the offer."
                )

                raw_fees = {
                    "verified": True,
                    "academic_year": YEAR,
                    "source_fee_category": "International",
                    "fee_band": band,
                    "programme_fee_group": "research",
                    "course_kind": "postgraduate_research",
                    "research_award": award,
                    "course_source_url": page["response_url"],
                    "annual_tuition": str(fee),
                    "currency": "GBP",
                    "study_year": 1,
                    "fee_method": "reviewed_course_and_published_band",
                    "scholarship_status": "pending_2026_27_review",
                    "deposit_status": "pending_2026_27_review",
                    "supplementary_charges_included": False,
                    "annual_increases_possible": True,
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
                        "start_month": "January",
                        "start_year": 2027,
                        "course_url": page["response_url"],
                    },
                    "tuition": {
                        "tuition_fee": str(fee),
                        "currency": "GBP",
                    },
                    "source": {
                        "academic_year": YEAR,
                        "source_url": FEE_URL,
                        "source_course_name": title,
                        "source_course_code": "",
                        "fee_basis": "annual_tuition",
                        "raw_fees": raw_fees,
                    },
                }

                normal_before = json.loads(json.dumps(before))
                normal_before["tuition"]["tuition_fee"] = str(
                    Decimal(str(tuition.tuition_fee)).quantize(
                        Decimal("0.01")
                    )
                )

                changed = normal_before != after

                proposals.append({
                    "course_id": course_id,
                    "changed": changed,
                    "before": before,
                    "after": after,
                })

                self.stdout.write(
                    f"{course_id}: GBP {tuition.tuition_fee} -> {fee}; "
                    f"January 2027 / {YEAR} — "
                    f"{'update' if changed else 'unchanged'}"
                )

            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "university_id": "UK002",
                        "academic_year": YEAR,
                        "mode": "apply" if options["apply"] else "preview",
                        "source_report": str(source_path),
                        "course_report": str(course_path),
                        "proposals": proposals,
                        "blocked": blocked,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            if options["apply"] and blocked:
                raise CommandError(
                    f"{len(blocked)} records require review. "
                    f"No batch changes applied. See {report_path}"
                )

            if options["apply"]:
                for proposal in proposals:
                    if not proposal["changed"]:
                        continue

                    course = Course.objects.get(pk=proposal["course_id"])
                    tuition = Tuition.objects.get(
                        course=course,
                        residency="INTL",
                    )
                    after = proposal["after"]

                    course.start_month = after["course"]["start_month"]
                    course.start_year = after["course"]["start_year"]
                    course.course_url = after["course"]["course_url"]
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

        self.stdout.write(f"\nMatched records: {len(proposals)}")
        self.stdout.write(
            f"Proposed changes: {sum(p['changed'] for p in proposals)}"
        )
        self.stdout.write(f"Blocked: {len(blocked)}")
        self.stdout.write(f"Report: {report_path}")
        self.stdout.write(
            "Applied reviewed research tuition, intake and source updates."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Scholarships, EU tuition and stored deposits unchanged."
        )