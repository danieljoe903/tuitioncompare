import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import (
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
)

from .import_abertay_remaining_fees import (
    SOURCE_FIELDS,
    fee_table,
    money,
    section,
    serialisable,
)


YEAR = "2026/27"

POLICY_URL = (
    "https://www.abertay.ac.uk/study-apply/"
    "money-fees-and-funding/tuition-fees-2026-2027/"
)

TARGETS = {
    "C_ABE001_RES_06": {
        "stored_title": "Food Science and Technology MSc by Research",
        "url": (
            "https://www.abertay.ac.uk/course-search/"
            "postgraduate-research/food-drink-science-technology/"
        ),
        "heading_words": ("food", "science", "technology"),
    },
    "C_ABE001_RES_08": {
        "stored_title": "Mental Health Nursing MSc by Research",
        "url": (
            "https://www.abertay.ac.uk/course-search/"
            "postgraduate-research/"
            "professional-development-in-mental-health-nursing-and-counselling/"
        ),
        "heading_words": ("mental", "health", "nursing", "counselling"),
    },
}

APPLICATION_NOTE = (
    "Published annual Masters by Research fee. "
    "The course page currently states that international "
    "Masters by Research/MPhil applications are not being "
    "accepted because of CAS availability. "
    "A published fee does not confirm that applications are open."
)


def clean_text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def course_snapshot(course):
    return {
        "title": course.title,
        "level": course.level,
        "duration": course.duration,
        "start_month": course.start_month,
        "start_year": course.start_year,
        "course_url": course.course_url,
        "is_active": course.is_active,
    }


class Command(BaseCommand):
    help = (
        "Reconcile two reviewed October 2026 Abertay "
        "research-course fee records. Preview by default."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        timestamp = datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )

        log_dir = root / "logs" / "abertay-fees"
        evidence_dir = log_dir / "source-evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        try:
            policy_path = root / "abertay_remaining_policy.json"
            policy_text = policy_path.read_text(
                encoding="utf-8-sig"
            )
            policy = json.loads(policy_text)

            if (
                policy["source_url"] != POLICY_URL
                or policy["page_title"] != "Tuition Fees (2026-2027)"
            ):
                raise ValueError("Unexpected fee-policy source.")

            pg_section = section(
                policy,
                "Postgraduate Tuition Fees - "
                "International Students 2026/27",
            )
            table = fee_table(
                pg_section,
                "POSTGRADUATE COURSES",
            )

            rows = [
                row
                for row in table[1:]
                if len(row) == 2
                and row[0] == "Masters by Research (all subjects)"
            ]

            if len(rows) != 1:
                raise ValueError(
                    "Expected one international Masters by Research fee."
                )

            raw_fee = rows[0][1]
            fee = money(raw_fee)

            if fee != Decimal("22000.00"):
                raise ValueError(
                    "Published research fee changed; review required."
                )

        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            ArithmeticError,
        ) as exc:
            raise CommandError(
                f"Fee-policy validation failed: {exc}"
            ) from exc

        evidence = {}

        # Complete network requests before opening a DB transaction.
        try:
            with requests.Session() as session:
                session.headers.update({
                    "User-Agent": "TuitionCompareSourceInspector/1.0"
                })

                for course_id, target in TARGETS.items():
                    response = session.get(
                        target["url"],
                        timeout=(15, 60),
                    )
                    response.raise_for_status()

                    if (
                        response.url.rstrip("/")
                        != target["url"].rstrip("/")
                    ):
                        raise ValueError(
                            f"{course_id}: unexpected redirect."
                        )

                    soup = BeautifulSoup(
                        response.text,
                        "html.parser",
                    )
                    heading = soup.find("h1")

                    if heading is None:
                        raise ValueError(
                            f"{course_id}: missing course heading."
                        )

                    source_title = clean_text(heading)

                    if not all(
                        word in source_title.casefold()
                        for word in target["heading_words"]
                    ):
                        raise ValueError(
                            f"{course_id}: research-area heading changed."
                        )

                    if len(source_title) > 255:
                        raise ValueError(
                            f"{course_id}: source title is too long."
                        )

                    details = {}

                    for item in soup.select("div.e-item"):
                        label = item.find("h4")
                        value = item.find("span", recursive=False)

                        if label is not None and value is not None:
                            details[clean_text(label)] = clean_text(value)

                    if "msc by research" not in details.get(
                        "Award Title", ""
                    ).casefold():
                        raise ValueError(
                            f"{course_id}: MSc by Research award unconfirmed."
                        )

                    if "1 year full-time" not in details.get(
                        "Duration", ""
                    ).casefold():
                        raise ValueError(
                            f"{course_id}: one-year duration unconfirmed."
                        )

                    if "october" not in details.get(
                        "Start Date", ""
                    ).casefold():
                        raise ValueError(
                            f"{course_id}: October intake unconfirmed."
                        )

                    page_text = clean_text(soup).casefold()

                    if (
                        "not accepting international applications"
                        not in page_text
                        or "cas" not in page_text
                    ):
                        raise ValueError(
                            f"{course_id}: application-status notice "
                            "changed; inspect before continuing."
                        )

                    digest = hashlib.sha256(
                        response.content
                    ).hexdigest()

                    snapshot_path = (
                        evidence_dir
                        / f"{course_id}-{digest[:12]}.html"
                    )
                    snapshot_path.write_text(
                        response.text,
                        encoding="utf-8",
                    )

                    evidence[course_id] = {
                        "source_title": source_title,
                        "source_url": response.url,
                        "snapshot": str(snapshot_path),
                        "sha256": digest,
                        "details": details,
                    }

        except (
            requests.RequestException,
            OSError,
            ValueError,
        ) as exc:
            raise CommandError(
                f"Course-page validation failed: {exc}"
            ) from exc

        plans = []

        with transaction.atomic():
            for course_id, target in TARGETS.items():
                try:
                    course = (
                        Course.objects.select_for_update()
                        .get(
                            course_id=course_id,
                            university__name__iexact="Abertay University",
                            university__country="UK",
                        )
                    )
                except Course.DoesNotExist as exc:
                    raise CommandError(
                        f"{course_id}: course not found."
                    ) from exc

                if (
                    course.title != target["stored_title"]
                    or course.level != "MRES"
                    or course.start_year != 2026
                    or (course.start_month or "").strip().casefold()
                    != "october"
                    or (course.duration or "").strip().casefold()
                    not in {"1 year", "1 years"}
                    or not course.is_active
                ):
                    raise CommandError(
                        f"{course_id}: database identity/intake "
                        "changed since review."
                    )

                tuitions = list(
                    Tuition.objects.select_for_update().filter(
                        course=course,
                        residency="INTL",
                    )
                )

                if len(tuitions) != 1:
                    raise CommandError(
                        f"{course_id}: expected one INTL tuition."
                    )

                tuition = tuitions[0]

                if tuition.currency != "GBP":
                    raise CommandError(
                        f"{course_id}: expected GBP."
                    )

                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                if source and source.academic_year != YEAR:
                    raise CommandError(
                        f"{course_id}: existing source belongs "
                        "to another academic year."
                    )

                old_metadata = (
                    {
                        field: getattr(source, field)
                        for field in SOURCE_FIELDS
                    }
                    if source else None
                )

                metadata = {
                    "academic_year": YEAR,
                    "source_url": POLICY_URL,
                    "source_course_code": "",
                    "source_course_name": (
                        evidence[course_id]["source_title"]
                    ),
                    "fee_basis": "annual_tuition",
                    "student_contribution": None,
                    "published_total": None,
                    "raw_fees": {
                        "fee_status": "International",
                        "source_fee_category": "International",
                        "currency": "GBP",
                        "annual_tuition": raw_fee,
                        "course_kind": "masters_by_research",
                        "course_source_url": target["url"],
                        "fee_method": "reviewed_research_subject",
                        "international_applications_open": False,
                        "review_note": APPLICATION_NOTE,
                        "fee_categories": [
                            {
                                "source_fee_category": "International",
                                "academic_year": YEAR,
                                "currency": "GBP",
                                "raw_annual_fee": raw_fee,
                                "annual_tuition": str(fee),
                                "footnote_marker": "",
                                "annotation": APPLICATION_NOTE,
                                "amount_requires_review": False,
                            }
                        ],
                    },
                }

                before_course = course_snapshot(course)
                after_course = dict(before_course)
                after_course["course_url"] = target["url"]

                before = serialisable({
                    "course": before_course,
                    "tuition_fee": tuition.tuition_fee,
                    "source_metadata": old_metadata,
                })
                after = serialisable({
                    "course": after_course,
                    "tuition_fee": fee,
                    "source_metadata": metadata,
                })

                changed = (
                    before_course != after_course
                    or tuition.tuition_fee != fee
                    or serialisable(old_metadata)
                    != serialisable(metadata)
                )

                plans.append({
                    "course": course,
                    "tuition": tuition,
                    "metadata": metadata,
                    "before": before,
                    "after": after,
                    "changed": changed,
                })

            report = {
                "mode": "apply" if options["apply"] else "preview",
                "database_changes": False,
                "committed": False,
                "academic_year": YEAR,
                "policy_url": POLICY_URL,
                "policy_sha256": hashlib.sha256(
                    policy_text.encode("utf-8")
                ).hexdigest(),
                "evidence": evidence,
                "proposals": [
                    {
                        "course_id": plan["course"].course_id,
                        "changed": plan["changed"],
                        "before": plan["before"],
                        "after": plan["after"],
                    }
                    for plan in plans
                ],
            }

            report_path = (
                log_dir / f"research-batch-{timestamp}.json"
            )
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            for plan in plans:
                course = plan["course"]
                tuition = plan["tuition"]

                self.stdout.write(
                    f"{course.course_id}: "
                    f"GBP {tuition.tuition_fee} -> {fee} — "
                    f"{'update' if plan['changed'] else 'unchanged'}"
                )

                if not options["apply"] or not plan["changed"]:
                    continue

                if course.course_url != TARGETS[
                    course.course_id
                ]["url"]:
                    course.course_url = TARGETS[
                        course.course_id
                    ]["url"]
                    course.save(
                        update_fields=["course_url", "updated_at"]
                    )

                tuition.tuition_fee = fee
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

        if options["apply"]:
            report["database_changes"] = any(
                plan["changed"] for plan in plans
            )
            report["committed"] = True

            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        self.stdout.write(
            f"\nProposed/updated records: "
            f"{sum(plan['changed'] for plan in plans)}"
        )
        self.stdout.write(
            "International applications currently closed "
            "according to the inspected course pages."
        )
        self.stdout.write(f"Report: {report_path}")

        self.stdout.write(
            "Applied course URL, tuition and source updates."
            if options["apply"]
            else "Preview only. No database records changed."
        )