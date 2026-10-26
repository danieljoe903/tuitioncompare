import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import (
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)


FIELDS = (
    "title",
    "level",
    "duration",
    "start_month",
    "start_year",
    "course_url",
    "ucas_cao_code",
)

COURSES = {
    "C_UK002_07": {
        "title": "BSc (Hons) Criminology and Sociology",
        "official_title": "Criminology and Sociology",
        "code": "ML93",
        "award": "BSC",
        "level": "UG",
        "years": 3,
        "path": "/undergraduate/ML93-criminology-and-sociology/",
        "reason": (
            "The course identity is confirmed, but its joint-course "
            "international fee band needs confirmation."
        ),
        "application_status": "not_assessed",
    },
    "C_UK002_14": {
        "title": "BVSc Veterinary Science",
        "official_title": "Veterinary Science (BVSc)",
        "code": "YD105",
        "award": "NQUG",
        "level": "UG",
        "years": 5,
        "path": "/undergraduate/vet-sci/",
        "reason": (
            "Joint Aberystwyth/RVC BVSc. The course directs students "
            "to the RVC fee schedule; no ordinary Aberystwyth science "
            "fee has been assigned."
        ),
        "application_status": "not_assessed",
    },
    "C_UK002_36": {
        "title": "BSc (Hons) Nursing (Adult)",
        "official_title": "Nursing (Adult)",
        "code": "B740",
        "award": "BSC",
        "level": "UG",
        "years": 3,
        "path": "/undergraduate/nursing-adult/",
        "reason": (
            "The saved course page says international applications "
            "for 2027 entry are held pending HEIW funding confirmation. "
            "An applicable international tuition amount remains unconfirmed."
        ),
        "application_status": "international_funding_confirmation_pending",
        "required_text": (
            "awaiting confirmation",
            "fund",
            "international",
            "2027",
        ),
    },
    "C_UK002_37": {
        "title": "BSc (Hons) Nursing (Mental Health)",
        "official_title": "Nursing (Mental Health)",
        "code": "B760",
        "award": "BSC",
        "level": "UG",
        "years": 3,
        "path": "/undergraduate/mental-health/",
        "reason": (
            "The saved course page excludes international applicants "
            "ordinarily resident outside the UK. Eligibility for a "
            "UK-resident applicant and the applicable fee require confirmation."
        ),
        "application_status": "restricted_by_ordinary_residence",
        "required_text": (
            "not currently available",
            "international applicants",
            "ordinarily resident outside",
        ),
    },
    "C_UK002_57": {
        "title": "BSc (Hons) Psychology and Marketing",
        "official_title": "Psychology and Marketing",
        "code": "NC58",
        "award": "BSC",
        "level": "UG",
        "years": 3,
        "path": "/undergraduate/marketing-psychology/",
        "reason": (
            "The course identity is confirmed, but the international "
            "fee band for this joint Psychology/Business course "
            "needs confirmation."
        ),
        "application_status": "not_assessed",
    },
    "C_UK002_91": {
        "title": "MSc Behaviour Change",
        "official_title": "Behaviour Change",
        "code": "C801",
        "award": "MSC",
        "level": "PG",
        "years": 1,
        "path": "/postgraduate/C801-behaviour-change/",
        "reason": (
            "The multidisciplinary course identity is confirmed. "
            "Its course-specific international fee band needs confirmation."
        ),
        "application_status": "not_assessed",
    },
}


def normalise(value):
    return " ".join(str(value).split())


def read_report(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise CommandError(f"Cannot read evidence report: {exc}") from exc


def find_report(root, supplied):
    if supplied:
        path = Path(supplied)
        if not path.is_absolute():
            path = root / path
        if not path.is_file():
            raise CommandError(f"Evidence report not found: {path}")
        return path

    folder = root / "logs" / "aberystwyth"
    matches = []

    for path in folder.glob("remaining-review-*/remaining-course-review.json"):
        report = read_report(path)
        ids = {
            row.get("course_id")
            for row in report.get("records", [])
            if isinstance(row, dict)
        }
        if set(COURSES).issubset(ids):
            matches.append(path)

    if not matches:
        raise CommandError(
            "No collected remaining-course report covers all six courses. "
            "Pass its path using --evidence."
        )

    return max(matches, key=lambda path: path.parent.name)


def validate_report(report):
    if report.get("university_id") != "UK002":
        raise CommandError("Evidence report is not for UK002.")

    try:
        generated = datetime.fromisoformat(
            report["generated_at"].replace("Z", "+00:00")
        )
        if generated.tzinfo is None:
            raise ValueError("Timestamp has no timezone.")
    except (KeyError, TypeError, ValueError) as exc:
        raise CommandError("Invalid evidence timestamp.") from exc

    now = datetime.now(timezone.utc)

    if generated > now + timedelta(minutes=5):
        raise CommandError("Evidence timestamp is in the future.")

    if now - generated > timedelta(days=7):
        raise CommandError(
            "Evidence is older than seven days. Refresh the source review "
            "before applying these time-sensitive intake restrictions."
        )


def course_values(course):
    return {field: getattr(course, field) for field in FIELDS}


def source_values(source):
    return {
        "academic_year": source.academic_year,
        "source_url": source.source_url,
        "source_course_name": source.source_course_name,
        "source_course_code": source.source_course_code,
        "fee_basis": source.fee_basis,
        "raw_fees": copy.deepcopy(source.raw_fees),
    }


def snapshot(course, tuition, source):
    return {
        "course": course_values(course),
        "tuition": {
            "tuition_fee": str(tuition.tuition_fee),
            "currency": tuition.currency,
            "scholarship": str(tuition.scholarship),
            "tuition_deposit": (
                str(tuition.tuition_deposit)
                if tuition.tuition_deposit is not None
                else None
            ),
        },
        "source": source_values(source),
    }


class Command(BaseCommand):
    help = (
        "Reconcile six confirmed Aberystwyth course identities and "
        "save unresolved fee/application restrictions."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--evidence",
            help="Path to the collected remaining-course-review JSON.",
        )

    def build_plan(self, report, lock=False):
        records = report.get("records")
        if not isinstance(records, list):
            raise CommandError("Evidence records must be a list.")

        plans = []
        blocked = []

        for course_id, config in COURSES.items():
            try:
                matching_records = [
                    row for row in records
                    if row.get("course_id") == course_id
                ]
                if len(matching_records) != 1:
                    raise ValueError("A unique evidence record is required.")

                record = matching_records[0]
                url = "https://courses.aber.ac.uk" + config["path"]

                candidates = [
                    candidate
                    for candidate in record.get("candidates", [])
                    if candidate.get("source_url", "").rstrip("/")
                    == url.rstrip("/")
                ]
                if len(candidates) != 1:
                    raise ValueError("A unique matching course page is required.")

                evidence = candidates[0].get("evidence", {})
                if evidence.get("status") != "collected":
                    raise ValueError("The course page was not collected.")

                if normalise(evidence.get("title", "")).casefold() != (
                    config["official_title"].casefold()
                ):
                    raise ValueError("Official page title does not match.")

                page_text = normalise(evidence.get("text", ""))
                lower = page_text.casefold()

                token = f'{config["code"]}-{config["award"]}'
                if token.casefold() not in lower:
                    raise ValueError(f"Missing source course identifier: {token}")

                if "september start 2027" not in lower:
                    raise ValueError("September 2027 intake is not evidenced.")

                duration_pattern = (
                    rf"\bcourse duration\s+{config['years']}\s+years?\b"
                )
                if not re.search(duration_pattern, lower):
                    raise ValueError("Published duration does not match.")

                for required in config.get("required_text", ()):
                    if required not in lower:
                        raise ValueError(
                            f"Application restriction not evidenced: {required}"
                        )

                queryset = Course.objects.filter(
                    course_id=course_id,
                    university_id="UK002",
                    is_active=True,
                )
                if lock:
                    queryset = queryset.select_for_update()
                course = queryset.get()

                stored = record.get("stored", {})
                if course.title not in {
                    stored.get("title"),
                    config["title"],
                }:
                    raise ValueError("Current title differs from reviewed titles.")

                if Course.objects.filter(
                    university_id="UK002",
                    is_active=True,
                    course_url=url,
                ).exclude(course_id=course_id).exists():
                    raise ValueError(
                        "Another active course already uses this source URL."
                    )

                tuitions = Tuition.objects.filter(
                    course=course,
                    residency="INTL",
                )
                if lock:
                    tuitions = tuitions.select_for_update()
                tuitions = list(tuitions)

                if len(tuitions) != 1 or tuitions[0].currency != "GBP":
                    raise ValueError(
                        "A unique GBP international tuition is required."
                    )
                tuition = tuitions[0]

                sources = TuitionSource.objects.filter(tuition=tuition)
                if lock:
                    sources = sources.select_for_update()
                source = sources.get()

                if not isinstance(source.raw_fees, dict):
                    raise ValueError("Source metadata must be a JSON object.")

                if source.raw_fees.get("verified") is True:
                    raise ValueError(
                        "Fee is already verified; this review command "
                        "will not downgrade it."
                    )

                before = snapshot(course, tuition, source)
                after = copy.deepcopy(before)

                after["course"].update({
                    "title": config["title"],
                    "level": config["level"],
                    "duration": f'{config["years"]} '
                                f'{"Year" if config["years"] == 1 else "Years"}',
                    "start_month": "September",
                    "start_year": 2027,
                    "course_url": url,
                })

                # Course Code is not necessarily a UCAS code:
                # YD105 is retained as a source code, not assigned as UCAS.
                if config["level"] == "UG" and config["award"] == "BSC":
                    after["course"]["ucas_cao_code"] = config["code"]

                raw = after["source"]["raw_fees"]
                raw.update({
                    "verified": False,
                    "fee_verification_status": "needs_review",
                    "course_source_url": url,
                    "course_identity": {
                        "status": "confirmed_current_catalogue_match",
                        "official_title": config["official_title"],
                        "official_code": config["code"],
                        "official_award": config["award"],
                        "source_url": url,
                        "evidence_generated_at": report["generated_at"],
                    },
                    "fee_review": {
                        "status": "pending",
                        "target_academic_year": "2027/28",
                        "reason": config["reason"],
                    },
                    "application_review": {
                        "status": config["application_status"],
                        "source_url": url,
                        "evidence_generated_at": report["generated_at"],
                        "note": config["reason"],
                    },
                    "scholarship_status": "applicability_unconfirmed",
                    "deposit_status": "individual_policy_review_required",
                    "display_notes": {
                        "fee": config["reason"],
                        "scholarship": (
                            "Scholarship eligibility has not been confirmed "
                            "for this course; no verified deduction established."
                        ),
                        "deposit": (
                            "A course-applicable deposit amount has not "
                            "been confirmed. Check the university's offer."
                        ),
                    },
                })

                # Do not label the existing stored price as a verified
                # 2027/28 fee merely because the course intake was corrected.
                after["source"].update({
                    "source_course_name": config["official_title"],
                    "source_course_code": config["code"],
                    "fee_basis": "unverified",
                })

                plans.append({
                    "course_id": course_id,
                    "changed": before != after,
                    "before": before,
                    "after": after,
                    "course": course,
                    "tuition": tuition,
                    "source": source,
                })

            except (
                ValueError,
                Course.DoesNotExist,
                TuitionSource.DoesNotExist,
            ) as exc:
                blocked.append({
                    "course_id": course_id,
                    "reason": str(exc),
                })

        return plans, blocked

    def write_report(self, root, evidence_path, plans, blocked, apply):
        now = datetime.now(timezone.utc)
        folder = root / "logs" / "aberystwyth"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (
            "six-course-review-"
            + now.strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )

        report = {
            "generated_at": now.isoformat(),
            "mode": "apply_requested" if apply else "preview",
            "university_id": "UK002",
            "evidence_report": str(evidence_path),
            "records": [
                {
                    key: plan[key]
                    for key in ("course_id", "changed", "before", "after")
                }
                for plan in plans
            ],
            "blocked": blocked,
        }

        path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        evidence_path = find_report(root, options.get("evidence"))
        evidence = read_report(evidence_path)
        validate_report(evidence)

        # Lock and re-check records inside the applying transaction.
        with transaction.atomic():
            if options["apply"]:
                University.objects.select_for_update().get(
                    university_id="UK002"
                )

            plans, blocked = self.build_plan(
                evidence,
                lock=options["apply"],
            )

            report_path = self.write_report(
                root,
                evidence_path,
                plans,
                blocked,
                options["apply"],
            )

            for plan in plans:
                status = "update" if plan["changed"] else "unchanged"
                self.stdout.write(f'{plan["course_id"]}: {status}')

                if plan["changed"]:
                    for field in FIELDS:
                        old = plan["before"]["course"][field]
                        new = plan["after"]["course"][field]
                        if old != new:
                            self.stdout.write(f"  {field}: {old} -> {new}")

                self.stdout.write(
                    "  Review: "
                    + plan["after"]["source"]["raw_fees"]["fee_review"]["reason"]
                )

            for item in blocked:
                self.stderr.write(
                    f'{item["course_id"]}: BLOCKED — {item["reason"]}'
                )

            self.stdout.write(f"\nMatched records: {len(plans)}")
            self.stdout.write(
                "Proposed changes: "
                + str(sum(plan["changed"] for plan in plans))
            )
            self.stdout.write(f"Blocked: {len(blocked)}")
            self.stdout.write(f"Report: {report_path}")

            if blocked:
                raise CommandError(
                    "Review stopped. No database changes were applied."
                )

            if not options["apply"]:
                self.stdout.write(
                    "Preview only. No database records changed."
                )
                return

            for plan in plans:
                if not plan["changed"]:
                    continue

                course = plan["course"]
                changed_fields = []

                for field, value in plan["after"]["course"].items():
                    if getattr(course, field) != value:
                        setattr(course, field, value)
                        changed_fields.append(field)

                if changed_fields:
                    course.save(
                        update_fields=[*changed_fields, "updated_at"]
                    )

                source = plan["source"]
                for field, value in plan["after"]["source"].items():
                    setattr(source, field, value)

                source.save(
                    update_fields=[
                        *plan["after"]["source"].keys(),
                        "checked_at",
                    ]
                )

                TuitionUpdateAudit.objects.create(
                    tuition=plan["tuition"],
                    course_identifier=plan["course_id"],
                    residency="INTL",
                    before=plan["before"],
                    after=plan["after"],
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Course corrections and review restrictions saved."
            )
        )
        self.stdout.write(
            "Tuition prices, stored scholarships and deposits unchanged. "
            "These six fees remain unresolved."
        )