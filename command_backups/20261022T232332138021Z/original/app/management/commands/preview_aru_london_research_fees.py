import copy
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.models import Course, Tuition, TuitionSource
from django.db import transaction

from app.models import TuitionUpdateAudit


TARGETS = {
    "C_UK003_19": {
        "title": "MSc Accounting and Financial Management",
        "slug": "accounting-and-financial-management",
        "location": "ARU London",
        "duration": "1 Year",
        "month": "September",
        "year": 2027,
        "academic_year": "2027/28",
        "amount": "20925.00",
        "basis": "full_time_course_fee",
        "source_basis": "total cost",
    },
    "C_UK003_24": {
        "title": "LLM International Law",
        "slug": "international-law-london",
        "location": "ARU London",
        "duration": "1 Year",
        "month": "September",
        "year": 2027,
        "academic_year": "2027/28",
        "amount": "20925.00",
        "basis": "annual_tuition",
        "source_basis": "full-time, per year",
    },
    "C_UK003_29": {
        "title": "MSc International Project Management",
        "slug": "international-project-management",
        "location": "ARU London",
        "duration": "1 Year",
        "month": "September",
        "year": 2027,
        "academic_year": "2027/28",
        "amount": "20925.00",
        "basis": "annual_tuition",
        "source_basis": "full-time, per year",
    },
    "C_UK003_96": {
        "title": "MSc International Hospitality and Tourism Management",
        "slug": "international-hospitality-and-management",
        "location": "ARU London",
        "duration": "1 Year",
        "month": "September",
        "year": 2027,
        "academic_year": "2027/28",
        "amount": "20925.00",
        "basis": "annual_tuition",
        "source_basis": "full-time, per year",
    },
    "C_ARU003_RES_07": {
        "title": "MPhil Education",
        "old_title": "MPhil (Research) Education",
        "slug": "school-of-education",
        "location": "Chelmsford Campus",
        "duration": "1 to 3 Years",
        "month": "January",
        "year": 2027,
        "academic_year": "2026/27",
        "amount": "17500.00",
        "basis": "annual_tuition",
        "research": True,
    },
    "C_UK003_26": {
        "title": "Master of Public Health (MPH)",
        "slug": "master-of-public-health-london",
        "location": "ARU London",
        "duration": "1 Year",
        "month": "September",
        "year": 2027,
        "academic_year": "",
        "amount": None,
        "basis": "unavailable",
        "reason": (
            "The reviewed ARU London Public Health page does "
            "not provide a confirmed international tuition "
            "amount. International tuition is unavailable "
            "pending confirmation; the stored amount must "
            "not be presented as verified."
        ),
    },
}


def money(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid monetary amount.")
    return f"{amount:.2f}"


class Command(BaseCommand):
    help = (
        "Preview five ARU fee updates and a Public Health "
        "campus correction. No database records are changed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--evidence",
            required=True,
            help="Path to the six-course evidence report.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply validated course and tuition updates.",
        )

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        evidence_path = Path(options["evidence"])

        if not evidence_path.is_absolute():
            evidence_path = root / evidence_path

        try:
            evidence = json.loads(
                evidence_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError) as exc:
            raise CommandError(
                f"Cannot read evidence: {exc}"
            ) from exc

        if (
            not isinstance(evidence, dict)
            or evidence.get("university_id") != "UK003"
            or not isinstance(evidence.get("pages"), list)
        ):
            raise CommandError("Invalid ARU evidence report.")

        allowed_issue = {
            "course_id": "C_UK003_26",
            "error": (
                "No application link found; "
                "manual route review required."
            ),
        }

        for issue in evidence.get("errors", []):
            if issue != allowed_issue:
                raise CommandError(
                    "Unexpected collection issue: "
                    + json.dumps(issue, ensure_ascii=False)
                )

        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "preview",
            "database_changes": False,
            "university_id": "UK003",
            "evidence_report": str(evidence_path),
            "records": [],
            "blocked": [],
        }

        for course_id, target in TARGETS.items():
            try:
                plan = self.plan_record(
                    evidence_path.parent,
                    evidence["pages"],
                    course_id,
                    target,
                )
                report["records"].append(plan)
            except (
                CommandError,
                OSError,
                ValueError,
                TypeError,
                KeyError,
            ) as exc:
                report["blocked"].append({
                    "course_id": course_id,
                    "reason": str(exc),
                })

        report["verified_fee_proposals"] = sum(
            entry["after"]["source"]["raw_fees"]["verified"]
            for entry in report["records"]
        )
        report["unavailable_fee_proposals"] = sum(
            not entry["after"]["source"]["raw_fees"]["verified"]
            for entry in report["records"]
        )
        report["proposed_changes"] = sum(
            entry["changed"] for entry in report["records"]
        )

        folder = root / "logs" / "aru"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / (
            "london-research-preview-"
            + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )

        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if options["apply"] and not report["blocked"]:
            self.apply_records(
                report,
                evidence_path.parent,
                evidence["pages"]
            )
            report["mode"] = "apply"
            report["database_changes"] = any(
                entry["changed"]
                for entry in report["records"]
            )
            output.write_text(
                json.dumps(
                    report,
                    indent=2,
                    ensure_ascii=False
                ),encoding="utf-8"
            )
        for entry in report["records"]:
            target = TARGETS[entry["course_id"]]
            state = "update" if entry["changed"] else "unchanged"

            if target["amount"] is None:
                self.stdout.write(
                    f"{entry['course_id']}: campus correction; "
                    f"international fee unavailable — {state}"
                )
            else:
                self.stdout.write(
                    f"{entry['course_id']}: GBP "
                    f"{entry['before']['tuition']['tuition_fee']} "
                    f"-> {target['amount']}; "
                    f"{target['academic_year']}; "
                    f"{target['basis']} — {state}"
                )

            for field, value in entry["after"]["course"].items():
                old = entry["before"]["course"][field]
                if old != value:
                    self.stdout.write(
                        f"  {field}: {old} -> {value}"
                    )

        for entry in report["blocked"]:
            self.stderr.write(
                f"{entry['course_id']}: {entry['reason']}"
            )

        self.stdout.write(
            f"\nVerified fee proposals: "
            f"{report['verified_fee_proposals']}"
        )
        self.stdout.write(
            f"Unavailable fee proposals: "
            f"{report['unavailable_fee_proposals']}"
        )
        self.stdout.write(
            f"Blocked: {len(report['blocked'])}"
        )
        self.stdout.write(f"Report: {output}")
        if options["apply"] and not report["blocked"]:
            self.stdout.write(
                "Reviewed course corrections, tuition and "
                "source metadata saved."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )
        self.stdout.write(
            "Scholarship rules and deposit scopes are not "
            "extended by this command."
        )

        if report["blocked"]:
            raise CommandError(
                "Some proposals require review. "
                "See the saved report."
            )

    def validated_page(self, folder, pages, url):
        matches = [
            page
            for page in pages
            if page.get("source_url", "").rstrip("/") == url
        ]

        if len(matches) != 1:
            raise CommandError(
                f"Expected one source capture: {url}"
            )

        page = matches[0]
        filename = page.get("html_file")

        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
        ):
            raise CommandError("Invalid HTML evidence filename.")

        digest = hashlib.sha256(
            (folder / filename).read_bytes()
        ).hexdigest()

        if digest != page.get("sha256"):
            raise CommandError("Saved HTML hash mismatch.")

        return page

    def validate_route(self, page, target):
        text = " ".join(page.get("text", "").split())

        if target.get("research"):
            required = [
                "School of Education MPhil, PhD",
                "Chelmsford",
                "January 2027",
                "MPhil: Full-time, from 1 to 3 years.",
            ]

            if not all(value in text for value in required):
                raise CommandError(
                    "Education MPhil route or duration "
                    "was not confirmed."
                )

            values = {
                money(value.replace(",", ""))
                for value in re.findall(
                    r"International students,\s*2026/27\s*"
                    r"\(per year\)\s*£([\d,]+(?:\.\d{2})?)",
                    text,
                )
            }

            if values != {target["amount"]}:
                raise CommandError(
                    "Unexpected Education research fee: "
                    f"{sorted(values)}"
                )

            return

        # Restrict campus/date validation to the route overview,
        # excluding footer navigation and unrelated course links.
        marker = "End of main navigation."
        if marker not in text:
            raise CommandError(
                "Cannot isolate the course overview."
            )

        overview = text.split(marker, 1)[1]
        overview = overview.split(
            "2 years part-time It's possible", 1
        )[0]

        heading = target["title"]
        if heading.startswith(("MSc ", "LLM ")):
            heading = heading.split(" ", 1)[1]
        elif target["amount"] is None:
            heading = "Master of Public Health"

        if (
            heading not in overview
            or "Location ARU London" not in overview
            or "September 2027" not in overview
            or "Duration 1 year full-time" not in overview
        ):
            raise CommandError(
                "London route, duration or September intake "
                "was not confirmed."
            )

        if target["amount"] is None:
            # Do not silently retain an unavailable status if
            # this evidence already contains international fees.
            if re.search(
                r"£[\d,]+(?:\.\d{2})?\s+"
                r"International students starting",
                text,
            ):
                raise CommandError(
                    "Public Health contains an international "
                    "fee; review it before marking unavailable."
                )
            return

        values = {
            money(value.replace(",", ""))
            for value in re.findall(
                r"£([\d,]+(?:\.\d{2})?)\s+"
                r"International students starting 2027/28\s+"
                r"\(" + re.escape(target["source_basis"]) + r"\)",
                text,
            )
        }

        if values != {target["amount"]}:
            raise CommandError(
                "Unexpected London international fee: "
                f"{sorted(values)}"
            )

    def apply_records(self, report, folder, pages):
        with transaction.atomic():
            courses = {
                course.course_id: course
                for course in (
                    Course.objects.select_for_update()
                    .filter(
                        university_id="UK003",
                        course_id__in=TARGETS,
                    )
                    .order_by("course_id")
                )
            }

            if set(courses) != set(TARGETS):
                raise CommandError(
                    "Course records changed. Run preview again."
                )

            tuitions = list(
                Tuition.objects.select_for_update()
                .filter(
                    course_id__in=TARGETS,
                    residency="INTL",
                )
                .order_by("pk")
            )

            tuition_by_course = {}
            for tuition in tuitions:
                if tuition.course_id in tuition_by_course:
                    raise CommandError(
                        "Duplicate international tuition found."
                    )
                tuition_by_course[tuition.course_id] = tuition

            if set(tuition_by_course) != set(TARGETS):
                raise CommandError(
                    "International tuition records changed."
                )

            list(
                TuitionSource.objects.select_for_update()
                .filter(tuition__in=tuitions)
                .order_by("pk")
            )

            # Recheck evidence and database values while locked.
            # No writes occur until every proposal passes.
            for entry in report["records"]:
                course_id = entry["course_id"]
                current = self.plan_record(
                    folder,
                    pages,
                    course_id,
                    TARGETS[course_id],
                )

                if (
                    current["before"] != entry["before"]
                    or current["after"] != entry["after"]
                ):
                    raise CommandError(
                        f"{course_id}: values changed during "
                        "the run. Run preview again."
                    )

            for entry in report["records"]:
                if not entry["changed"]:
                    continue

                course_id = entry["course_id"]
                course = courses[course_id]
                tuition = tuition_by_course[course_id]
                after = entry["after"]

                for field, value in after["course"].items():
                    setattr(course, field, value)

                course.save(
                    update_fields=(
                        list(after["course"]) + ["updated_at"]
                    )
                )

                # Public Health has no verified international
                # amount: retain its numeric value, but save
                # explicit unavailable source metadata below.
                if TARGETS[course_id]["amount"] is not None:
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
                    course_identifier=course_id,
                    residency="INTL",
                    before=entry["before"],
                    after=after,
                )
    def plan_record(self, folder, pages, course_id, target):
        url = (
            "https://www.aru.ac.uk/study/postgraduate/"
            + target["slug"]
        )
        page = self.validated_page(folder, pages, url)
        self.validate_route(page, target)

        course = Course.objects.filter(
            university_id="UK003",
            course_id=course_id,
            is_active=True,
        ).first()

        if course is None:
            raise CommandError("Active database course not found.")

        if course.title not in {
            target["title"],
            target.get("old_title", target["title"]),
        }:
            raise CommandError(
                f"Unexpected stored title: {course.title}"
            )

        if target.get("research"):
            if (
                course.level not in {"PG", "MRES"}
                or course.location != "Chelmsford Campus"
            ):
                raise CommandError(
                    "Unexpected stored Education research route."
                )
        elif (
            course.level != "PG"
            or course.duration != "1 Year"
            or course.location not in {
                "Cambridge Campus",
                "Chelmsford Campus",
                "ARU London",
            }
        ):
            raise CommandError(
                "Unexpected stored London candidate route."
            )

        if course.work_placement:
            raise CommandError(
                "A placement route needs separate review."
            )

        tuitions = list(
            Tuition.objects.filter(
                course=course,
                residency="INTL",
            )
        )

        if len(tuitions) != 1 or tuitions[0].currency != "GBP":
            raise CommandError(
                "One GBP international tuition is required."
            )

        tuition = tuitions[0]
        source = TuitionSource.objects.filter(
            tuition=tuition
        ).first()

        fields = [
            "title",
            "level",
            "duration",
            "location",
            "start_month",
            "start_year",
            "intake_months",
            "course_url",
            "work_placement",
        ]

        before = {
            "course": {
                field: getattr(course, field)
                for field in fields
            },
            "tuition": {
                "tuition_fee": money(tuition.tuition_fee),
                "currency": tuition.currency,
            },
            "source": (
                {
                    "academic_year": source.academic_year,
                    "source_url": source.source_url,
                    "source_course_code": source.source_course_code,
                    "source_course_name": source.source_course_name,
                    "fee_basis": source.fee_basis,
                    "raw_fees": source.raw_fees,
                }
                if source
                else None
            ),
        }

        after = copy.deepcopy(before)
        after["course"].update({
            "title": target["title"],
            "level": "PG",
            "duration": target["duration"],
            "location": target["location"],
            "start_month": target["month"],
            "start_year": target["year"],
            "intake_months": target["month"],
            "course_url": url,
        })

        verified = target["amount"] is not None
        if verified:
            after["tuition"]["tuition_fee"] = target["amount"]

        # Replace obsolete fee metadata rather than leaving
        # previous verified amounts/categories attached.
        raw = {
            "verified": verified,
            "academic_year": target["academic_year"],
            "source_fee_category": "International",
            "currency": "GBP",
            "course_source_url": url,
            "source_sha256": page["sha256"],
            "campus": target["location"],
            "intake": (
                f"{target['month']} {target['year']}"
            ),
            "study_year": 1,
            "course_kind": (
                "postgraduate_research"
                if target.get("research")
                else "postgraduate_taught"
            ),
            "mapping_kind": (
                "research_route_correction"
                if target.get("research")
                else "campus_correction"
            ),
            "scholarship_status": "applicability_unconfirmed",
            "deposit_status": (
                "course_page_deposit_requires_reconciliation"
                if verified and not target.get("research")
                else "applicability_unconfirmed"
            ),
            "fee_categories": [],
        }

        if verified:
            # Compatibility key used by the current comparison
            # service. fee_basis determines the display label.
            raw["annual_tuition"] = target["amount"]
            raw["published_fee_amount"] = target["amount"]
            raw["published_fee_basis"] = target["basis"]
            raw["fee_categories"] = [{
                "source_fee_category": "International",
                "academic_year": target["academic_year"],
                "currency": "GBP",
                "annual_tuition": target["amount"],
                "fee_basis": target["basis"],
                "amount_requires_review": False,
            }]

            if target["basis"] == "full_time_course_fee":
                raw["fee_note"] = (
                    "Published international total course fee "
                    "for the one-year full-time ARU London "
                    "route, September 2027. This is a total "
                    "course fee, not a separately stated annual rate."
                )
            else:
                raw["fee_note"] = (
                    "Published annual international tuition "
                    f"for {target['academic_year']}, "
                    f"{target['month']} {target['year']} intake."
                )
        else:
            raw["verification_reason"] = target["reason"]
            raw["fee_note"] = target["reason"]

        if target.get("research"):
            raw["duration_note"] = (
                "Published full-time MPhil duration: "
                "from one to three years."
            )

        if verified and not target.get("research"):
            raw["published_deposit_evidence"] = {
                "amount": "4000.00",
                "currency": "GBP",
                "alternative": "sponsorship_letter",
                "policy_scope_extended": False,
                "source_url": url,
            }

            if not re.search(
                r"deposit of £4,000 or a sponsorship letter",
                page.get("text", ""),
            ):
                raise CommandError(
                    "Expected course-page deposit evidence "
                    "was not found."
                )

        after["source"] = {
            "academic_year": target["academic_year"],
            "source_url": url,
            "source_course_code": "",
            "source_course_name": target["title"],
            "fee_basis": target["basis"],
            "raw_fees": raw,
        }

        return {
            "course_id": course_id,
            "changed": before != after,
            "before": before,
            "after": after,
        }