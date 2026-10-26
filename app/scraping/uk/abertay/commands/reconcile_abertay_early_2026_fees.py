import hashlib
import json
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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

from app.scraping.uk.abertay.commands.import_abertay_remaining_fees import SOURCE_FIELDS, money, normalise, serialisable, taught_policy_rows


YEAR = "2025/26"

SOURCE_URL = (
    "https://www.abertay.ac.uk/study-apply/"
    "money-fees-and-funding/tuition-fees-2025-2026/"
)

PREVIOUS_SOURCE_URL = (
    "https://www.abertay.ac.uk/study-apply/"
    "money-fees-and-funding/tuition-fees-2026-2027/"
)

TAUGHT = {
    "C_UK001_47": (
        "MSc Accounting and Finance (CIMA Gateway)",
        "17000.00",
    ),
    "C_UK001_48": (
        "MSc Applied Artificial Intelligence & UX",
        "18000.00",
    ),
    "C_UK001_51": (
        "MSc Construction and Project Management",
        "17000.00",
    ),
    "C_UK001_52": (
        "MSc Ethical Hacking and Cybersecurity",
        "18000.00",
    ),
    "C_UK001_53": (
        "MSc Computer Games Technology",
        "18000.00",
    ),
    "C_UK001_54": (
        "Global MBA",
        "17000.00",
    ),
}

RESEARCH = {
    "C_UK001_71":
        "MSc by Research in Accounting and Finance",
    "C_UK001_73":
        "MSc by Research in Applied Games",
    "C_UK001_74":
        "MSc by Research in Business and Management",
    "C_UK001_75":
        "MSc by Research in Civil, Structural and "
        "Geotechnical Engineering",
    "C_UK001_84":
        "MSc by Research in Analytical, Sensory and "
        "Consumer Science",
    "C_UK001_85":
        "MSc by Research in Cybersecurity",
    "C_UK001_89":
        "MA Computer Arts by Research",
}


def text(element):
    return " ".join(
        element.get_text(" ", strip=True).split()
    )


def fetch_policy():
    response = requests.get(
        SOURCE_URL,
        headers={
            "User-Agent": "TuitionCompareSourceInspector/1.0",
        },
        timeout=(30, 60),
    )
    response.raise_for_status()

    if response.url.rstrip("/") != SOURCE_URL.rstrip("/"):
        raise ValueError(
            f"Unexpected redirect: {response.url}"
        )

    soup = BeautifulSoup(response.content, "html.parser")

    heading = soup.find("h1")

    if heading is None or not (
        "2025-2026" in text(heading)
        or "2025-26" in text(heading)
    ):
        raise ValueError(
            "Unexpected academic year on the source page."
        )

    headings = soup.find_all(
        lambda tag: tag.name in ("h2", "h3", "h4")
        and text(tag) == (
            "Postgraduate Tuition Fees - "
            "International Students 2025/26"
        )
    )

    if len(headings) != 1:
        raise ValueError(
            "Expected one international postgraduate section."
        )

    section = headings[0].find_parent(
        "div",
        class_="e-item",
    )

    if section is None:
        raise ValueError(
            "Could not isolate the international fee section."
        )

    tables = []

    for table in section.find_all("table"):
        rows = []

        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])

            if cells:
                rows.append([
                    text(cell) for cell in cells
                ])

        if (
            rows
            and len(rows[0]) == 2
            and rows[0][0].casefold()
            == "postgraduate courses"
            and "tuition fees per year"
            in rows[0][1].casefold()
        ):
            tables.append(rows)

    if len(tables) != 1:
        raise ValueError(
            "Expected one international postgraduate fee table."
        )

    return response.content, tables[0], text(section)


class Command(BaseCommand):
    help = (
        "Reconcile reviewed January/February 2026 "
        "international fees against the 2025/26 policy."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
        )

    def handle(self, *args, **options):
        try:
            html, table, policy_text = fetch_policy()

            research_rows = [
                row
                for row in table[1:]
                if len(row) == 2
                and row[0]
                == "Masters by Research (all subjects)"
            ]

            if len(research_rows) != 1:
                raise ValueError(
                    "Research fee is missing or ambiguous."
                )

            research_fee = money(research_rows[0][1])

            if str(research_fee) != "22000.00":
                raise ValueError(
                    "Research fee changed; review the source."
                )

        except (
            requests.RequestException,
            ValueError,
        ) as exc:
            raise CommandError(str(exc)) from exc

        root = Path(settings.BASE_DIR)
        log_dir = root / "logs" / "abertay-fees"
        evidence_dir = log_dir / "source-evidence"

        evidence_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        digest = hashlib.sha256(html).hexdigest()

        evidence_file = evidence_dir / (
            f"abertay-2025-26-{digest[:12]}.html"
        )
        evidence_file.write_bytes(html)

        plans = []
        expected_ids = set(TAUGHT) | set(RESEARCH)

        with transaction.atomic():
            courses = list(
                Course.objects.select_for_update()
                .filter(
                    course_id__in=expected_ids,
                    university__name__iexact=(
                        "Abertay University"
                    ),
                    university__country="UK",
                )
                .order_by("course_id")
            )

            if {
                course.course_id for course in courses
            } != expected_ids:
                raise CommandError(
                    "One or more reviewed courses are missing."
                )

            for course in courses:
                taught = course.course_id in TAUGHT
                expected_month = (
                    "january" if taught else "february"
                )

                if (
                    course.start_year != 2026
                    or course.start_month.strip().casefold()
                    != expected_month
                ):
                    raise CommandError(
                        f"{course.course_id}: intake changed."
                    )

                expected_title = (
                    TAUGHT[course.course_id][0]
                    if taught
                    else RESEARCH[course.course_id]
                )

                if normalise(course.title) != normalise(
                    expected_title
                ):
                    raise CommandError(
                        f"{course.course_id}: title changed."
                    )

                if taught:
                    if (
                        course.level not in {"PG", "PGT"}
                        or course.duration.strip().casefold()
                        != "1.3 years"
                    ):
                        raise CommandError(
                            f"{course.course_id}: "
                            "level or duration changed."
                        )

                    rows = taught_policy_rows(
                        table,
                        course.title,
                    )

                    if len(rows) != 1:
                        raise CommandError(
                            f"{course.course_id}: "
                            "named fee is ambiguous."
                        )

                    source_label, raw_fee = rows[0]
                    fee = money(raw_fee)

                    if str(fee) != TAUGHT[course.course_id][1]:
                        raise CommandError(
                            f"{course.course_id}: "
                            "published fee changed."
                        )

                    kind = "postgraduate_taught"

                else:
                    if (
                        course.level not in {"PG", "MRES"}
                        or course.duration.strip().casefold()
                        not in {"1 year", "1 years"}
                    ):
                        raise CommandError(
                            f"{course.course_id}: "
                            "research level or duration changed."
                        )

                    source_label, raw_fee = research_rows[0]
                    fee = research_fee
                    kind = "masters_by_research"

                fees = list(
                    Tuition.objects.select_for_update()
                    .filter(
                        course=course,
                        residency="INTL",
                    )
                )

                if len(fees) != 1:
                    raise CommandError(
                        f"{course.course_id}: "
                        "expected one international tuition."
                    )

                tuition = fees[0]

                if tuition.currency != "GBP":
                    raise CommandError(
                        f"{course.course_id}: currency is not GBP."
                    )

                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                if source and (
                    source.academic_year
                    not in {"2025/26", "2026/27"}
                    or source.source_url not in {
                        SOURCE_URL,
                        PREVIOUS_SOURCE_URL,
                    }
                ):
                    raise CommandError(
                        f"{course.course_id}: "
                        "existing source needs separate review."
                    )

                note = (
                    "2025/26 entrant fee for the stored "
                    f"{course.start_month} 2026 intake. "
                    "Course details and stored discounts "
                    "have not been changed. Scholarship "
                    "eligibility for this cohort requires "
                    "separate verified rules."
                )

                metadata = {
                    "academic_year": YEAR,
                    "source_url": SOURCE_URL,
                    "source_course_code": "",
                    "source_course_name": source_label,
                    "fee_basis": "annual_tuition",
                    "student_contribution": None,
                    "published_total": None,
                    "raw_fees": {
                        "fee_status": "International",
                        "source_fee_category": "International",
                        "currency": "GBP",
                        "annual_tuition": raw_fee,
                        "course_kind": kind,
                        "fee_method": "reviewed_cohort_policy",
                        "cohort_academic_year": YEAR,
                        "matched_database_title": course.title,
                        "policy_sha256": digest,
                        "policy_snapshot": str(evidence_file),
                        "review_note": note,
                        "fee_categories": [{
                            "source_fee_category": "International",
                            "academic_year": YEAR,
                            "currency": "GBP",
                            "raw_annual_fee": raw_fee,
                            "annual_tuition": str(fee),
                            "footnote_marker": "",
                            "annotation": note,
                            "amount_requires_review": False,
                        }],
                    },
                }

                old_metadata = (
                    {
                        field: getattr(source, field)
                        for field in SOURCE_FIELDS
                    }
                    if source else None
                )

                # Compare fee data separately from download evidence.
                old_comparison = serialisable(old_metadata)
                new_comparison = serialisable(metadata)

                for comparison in (
                    old_comparison,
                    new_comparison,
                ):
                    if comparison is None:
                        continue

                    raw = comparison.get("raw_fees", {})
                    raw.pop("policy_sha256", None)
                    raw.pop("policy_snapshot", None)

                changed = (
                    tuition.tuition_fee != fee
                    or old_comparison != new_comparison
                )

                # Keep the saved metadata when only download
                # evidence changed. The new HTML is still archived.
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
                plans.append({
                    "course": course,
                    "tuition": tuition,
                    "fee": fee,
                    "metadata": metadata,
                    "before": before,
                    "after": after,
                    "changed": changed,
                })

            report = {
                "academic_year": YEAR,
                "mode": (
                    "apply" if options["apply"] else "preview"
                ),
                "status": "prepared",
                "source_url": SOURCE_URL,
                "source_sha256": digest,
                "policy_text": policy_text,
                "proposals": [
                    {
                        "course_id": plan["course"].course_id,
                        "title": plan["course"].title,
                        "start_month": plan["course"].start_month,
                        "start_year": plan["course"].start_year,
                        "changed": plan["changed"],
                        "before": plan["before"],
                        "after": plan["after"],
                    }
                    for plan in plans
                ],
            }

            output = log_dir / (
                "early-2026-"
                + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
                + ".json"
            )

            output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            for plan in plans:
                course = plan["course"]
                tuition = plan["tuition"]

                self.stdout.write(
                    f"{course.course_id}: "
                    f"GBP {tuition.tuition_fee} -> "
                    f"{plan['fee']} — "
                    f"{'update' if plan['changed'] else 'unchanged'}"
                )

                if not options["apply"] or not plan["changed"]:
                    continue

                tuition.tuition_fee = plan["fee"]
                tuition.save(
                    update_fields=["tuition_fee"]
                )

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

        report["status"] = (
            "committed" if options["apply"] else "preview_only"
        )

        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        self.stdout.write(
            f"\nMatched records: {len(plans)}"
        )
        self.stdout.write(
            "Proposed/updated records: "
            f"{sum(plan['changed'] for plan in plans)}"
        )
        self.stdout.write(
            f"Before/after report: {output}"
        )
        self.stdout.write(
            "Applied tuition/source updates."
            if options["apply"]
            else "Preview only. No database records changed."
        )