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

from .import_aberystwyth_fees import (
    FEE_URL,
    has_department_link,
    latest_report,
    money,
    read_json,
    snapshot,
    validate_age,
)


ACADEMIC_YEAR = "2027/28"

# ID suffix: (official title, fee group, fee band, department)
#
# Fee bands come from the published subject classifications.
# BA/BSc and MA/MSc titles alone do not determine the fee band.
REVIEWED_COURSES = {
    "02": ("Agriculture", "bachelors", "Sciences", "/en/life-sciences"),
    "03": (
        "Marine and Freshwater Biology",
        "bachelors",
        "Sciences",
        "/en/life-sciences",
    ),
    "05": ("Computer Science", "bachelors", "Sciences", "/en/cs"),
    "06": (
        "Computer Science (with integrated year in industry)",
        "bachelors",
        "Sciences",
        "/en/cs",
    ),
    "08": ("Geography", "bachelors", "Sciences", "/en/dges"),
    "09": ("History", "bachelors", "Humanities", "/en/history"),
    "11": ("Law", "bachelors", "Humanities", "/en/als"),
    "12": ("Psychology", "bachelors", "Sciences", "/en/psychology"),
    "13": (
        "Sport and Exercise Science",
        "bachelors",
        "Sciences",
        "/en/life-sciences",
    ),
    "19": ("Mathematics", "bachelors", "Sciences", "/en/maths"),
    "20": ("Physics", "bachelors", "Sciences", "/en/phys"),
    "21": (
        "Advanced Computer Science",
        "masters",
        "Sciences",
        "/en/cs",
    ),
    "22": ("Data Science", "masters", "Sciences", "/en/cs"),
    "24": (
        "International Relations",
        "masters",
        "Humanities",
        "/en/interpol",
    ),
    "26": (
        "Livestock Science",
        "masters",
        "Sciences",
        "/en/life-sciences",
    ),
    "30": ("History of Wales", "masters", "Humanities", "/en/history"),
    "31": (
        "Animal Science",
        "bachelors",
        "Sciences",
        "/en/life-sciences",
    ),
    "32": (
        "Animal Behaviour",
        "bachelors",
        "Sciences",
        "/en/life-sciences",
    ),
    "33": (
        "Biochemistry",
        "bachelors",
        "Sciences",
        "/en/life-sciences",
    ),
    "34": ("Genetics", "bachelors", "Sciences", "/en/life-sciences"),
    "35": ("Ecology", "bachelors", "Sciences", "/en/life-sciences"),
    "38": (
        "Creative Writing",
        "bachelors",
        "Humanities",
        "/en/english",
    ),
    "39": (
        "English Literature and Creative Writing",
        "bachelors",
        "Humanities",
        "/en/english",
    ),
    "40": (
        "Media and Communication Studies",
        "bachelors",
        "Humanities",
        "/en/tfts",
    ),
    "42": (
        "Welsh (for Beginners)",
        "bachelors",
        "Humanities",
        "/en/cymraeg",
    ),
    "47": (
        "Engineering Management",
        "masters",
        "Sciences",
        "/en/phys",
    ),
    "48": ("Creative Writing", "masters", "Humanities", "/en/english"),
    "50": ("Literary Studies", "masters", "Humanities", "/en/english"),
    "51": (
        "Product Innovation and Entrepreneurship",
        "masters",
        "Sciences",
        "/en/phys",
    ),
    "55": (
        "Economics and International Relations",
        "bachelors",
        "Humanities",
        "/en/interpol",
    ),
    "56": (
        "Politics and Modern History",
        "bachelors",
        "Humanities",
        "/en/history",
    ),
    "58": ("Physical Geography", "bachelors", "Sciences", "/en/dges"),
    "59": ("Human Geography", "bachelors", "Sciences", "/en/dges"),
    "81": (
        "Environmental Earth Science",
        "bachelors",
        "Sciences",
        "/en/dges",
    ),
    "85": (
        "Data Science (with integrated year in industry)",
        "bachelors",
        "Sciences",
        "/en/maths",
    ),
    "88": (
        "Modern Languages",
        "bachelors",
        "Humanities",
        "/en/modernlangs",
    ),
}

EXPECTED_PRICES = {
    ("bachelors", "Humanities"): Decimal("20400.00"),
    ("bachelors", "Sciences"): Decimal("23255.00"),
    ("masters", "Humanities"): Decimal("22115.00"),
    ("masters", "Sciences"): Decimal("23820.00"),
}


def extract_prices(report):
    """Validate the collected central table against reviewed amounts."""
    fees = report.get("sources", {}).get("international_fees", {})

    if (
        fees.get("status") != "collected"
        or fees.get("response_url", "").rstrip("/")
        != FEE_URL.rstrip("/")
        or "Tuition Fees for International Students 2027/2028"
        not in fees.get("text", "")
    ):
        raise CommandError("Reviewed international fee evidence is missing.")

    matches = []

    for table in fees.get("tables", []):
        rows = table.get("rows", [])

        if not rows or len(rows[0]) < 3:
            continue

        headers = [str(cell).strip().casefold() for cell in rows[0]]

        if headers[1:3] != ["humanities", "sciences"]:
            continue

        prices = {}

        for group, prefix in (
            ("bachelors", "Bachelors Programme"),
            ("masters", "Masters Programme"),
        ):
            selected = [
                row
                for row in rows[1:]
                if row and str(row[0]).startswith(prefix)
            ]

            if len(selected) != 1 or len(selected[0]) < 3:
                break

            prices[(group, "Humanities")] = money(selected[0][1])
            prices[(group, "Sciences")] = money(selected[0][2])

        if prices == EXPECTED_PRICES:
            matches.append(prices)

    # The collected tables may contain several academic years.
    # Import only the uniquely matching, reviewed set of prices.
    if len(matches) != 1:
        raise CommandError(
            "Cannot uniquely identify the reviewed 2027/28 fee table. "
            "Refresh and inspect the official fee evidence."
        )

    return matches[0]


def validate_page(page, title, group, department):
    if not page or page.get("status") != "collected":
        return "Collected course-page evidence is missing."

    if page.get("title") != title:
        return "Official course title does not match the reviewed identity."

    parsed = urlsplit(page.get("response_url", ""))
    expected_prefix = (
        "/undergraduate/" if group == "bachelors" else "/postgraduate/"
    )

    if (
        parsed.scheme != "https"
        or parsed.netloc != "courses.aber.ac.uk"
        or not parsed.path.startswith(expected_prefix)
        or parsed.query
        or parsed.fragment
    ):
        return "Unexpected official course URL."

    if not has_department_link(page, department):
        return "Reviewed department association is missing."

    if page.get("advertised_intakes") != [
        {"month": "September", "year": 2027}
    ]:
        return "The reviewed September 2027 intake changed."

    return None


class Command(BaseCommand):
    help = (
        "Preview/apply the second reviewed Aberystwyth batch: "
        "36 September 2027 international tuition records."
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

        proposals = []
        blocked = []
        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        report_path = directory / f"batch-two-{stamp}.json"

        with transaction.atomic():
            try:
                university = University.objects.select_for_update().get(
                    pk="UK002",
                    country="UK",
                )
            except University.DoesNotExist as exc:
                raise CommandError("Aberystwyth UK002 was not found.") from exc

            if university.name.strip().casefold() != "aberystwyth university":
                raise CommandError("University identity mismatch.")

            for suffix, reviewed in REVIEWED_COURSES.items():
                course_id = f"C_UK002_{suffix}"
                title, group, band, department = reviewed
                page = pages.get(course_id)

                reason = validate_page(page, title, group, department)
                if reason:
                    blocked.append({"course_id": course_id, "reason": reason})
                    continue

                try:
                    course = Course.objects.select_for_update().get(
                        pk=course_id,
                        university=university,
                        is_active=True,
                    )
                except Course.DoesNotExist:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Active database course not found.",
                    })
                    continue

                expected_levels = (
                    {"UG"} if group == "bachelors" else {"PG", "PGT"}
                )

                if (
                    course.title != page.get("stored_title")
                    or course.duration != page.get("stored_duration")
                    or course.level != page.get("stored_level")
                    or course.level not in expected_levels
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Database course identity requires review.",
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
                fee = prices[(group, band)]

                before = snapshot(course, tuition, source)

                annotation = (
                    "First study-year tuition. Placement and study-abroad "
                    "years may have different charges; this is not a "
                    "whole-course total."
                )

                raw_fees = {
                    "verified": True,
                    "academic_year": ACADEMIC_YEAR,
                    "source_fee_category": "International",
                    "fee_band": band,
                    "programme_fee_group": group,
                    "course_kind": (
                        "undergraduate"
                        if group == "bachelors"
                        else "postgraduate_taught"
                    ),
                    "course_source_url": page["response_url"],
                    "annual_tuition": str(fee),
                    "currency": "GBP",
                    "study_year": 1,
                    "fee_method": "reviewed_course_and_published_band",
                    "scholarship_status": "pending_separate_reconciliation",
                    "deposit_status": "offer_letter_amount_required",
                    "fee_categories": [{
                        "source_fee_category": "International",
                        "academic_year": ACADEMIC_YEAR,
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
                        "course_url": page["response_url"],
                    },
                    "tuition": {
                        "tuition_fee": str(fee),
                        "currency": "GBP",
                    },
                    "source": {
                        "academic_year": ACADEMIC_YEAR,
                        "source_url": FEE_URL,
                        "source_course_name": title,
                        "source_course_code": "",
                        "fee_basis": "annual_tuition",
                        "raw_fees": raw_fees,
                    },
                }

                normal_before = json.loads(json.dumps(before))
                normal_before["tuition"]["tuition_fee"] = str(
                    Decimal(str(tuition.tuition_fee)).quantize(Decimal("0.01"))
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
                    f"September 2027 — "
                    f"{'update' if changed else 'unchanged'}"
                )

            report = {
                "generated_at": now.isoformat(),
                "mode": "apply" if options["apply"] else "preview",
                "database_changes_requested": bool(options["apply"]),
                "university_id": "UK002",
                "academic_year": ACADEMIC_YEAR,
                "source_report": str(source_path),
                "course_report": str(course_path),
                "proposals": proposals,
                "blocked": blocked,
            }

            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            # Apply this batch only when every reviewed course passed.
            if options["apply"] and blocked:
                raise CommandError(
                    f"{len(blocked)} batch records need review. "
                    f"No batch updates applied. See {report_path}"
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

        self.stdout.write(f"\nMatched batch records: {len(proposals)}")
        self.stdout.write(
            f"Proposed changes: {sum(p['changed'] for p in proposals)}"
        )
        self.stdout.write(f"Blocked in batch: {len(blocked)}")
        self.stdout.write(f"Report: {report_path}")

        self.stdout.write(
            "Applied international tuition, intake and source updates."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Scholarships, EU tuition and stored deposits were unchanged."
        )