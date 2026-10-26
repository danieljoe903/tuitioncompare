import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import (
    Course,
    Scholarship,
    ScholarshipRule,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)

from app.scraping.uk.aberystwyth.commands.import_aberystwyth_welsh_education_fees import COURSES, FEE_URL, download, text, verify_course, verify_fee_table


WELSH_ID = "C_ABE002_RES_09"
EDUCATION_ID = "C_UK002_68"

SCHOLARSHIP_URL = (
    "https://www.aber.ac.uk/en/study-with-us/international/"
    "scholarships/global-citizens-scholarship/"
)

AWARD_NAME = (
    "Global Citizens Scholarship — Humanities Research PG (2027/28)"
)


def verify_award_page(soup):
    heading = soup.find("h1")
    if heading is None or text(heading) != "Global Citizens Scholarship":
        raise ValueError("Wrong scholarship page.")

    matches = []

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = [
                text(cell)
                for cell in row.find_all(["th", "td"], recursive=False)
            ]

            if len(cells) < 4:
                continue

            label = cells[0].casefold()
            if not (
                "humanities" in label
                and "mphil" in label
                and "phd" in label
            ):
                continue

            values = [
                value.replace(",", "").replace(" ", "")
                for value in cells[1:4]
            ]

            if (
                values[0] == "£20270"
                and values[1] == "£3000"
                and values[2].startswith("£17270")
            ):
                matches.append(cells)

    if len(matches) != 1:
        raise ValueError(
            "A unique Humanities research award row "
            "£20,270 / £3,000 / £17,270 is required."
        )

    content = text(soup).casefold()

    for phrase in (
        "new student from the academic year 2027/28 onwards",
        "international fee-paying",
        "received an offer",
        "academic entry requirements",
        "external scholarships",
        "1st august",
        "distance learning",
    ):
        if phrase not in content:
            raise ValueError(
                f"Scholarship conditions need review: {phrase!r}."
            )

    return matches[0]


def desired_rules(course_ids):
    return {
        "benefit": "tuition",
        "kind": "fixed",
        "value": "3000.00",
        "currency": "GBP",
        "academic_year": "2027/28",
        "study_years": [1],
        "fee_basis_confirmed": True,
        "percentage_basis": "original_tuition",
        "award_status": "conditional",
        "stacking": "exclusive",
        "compatible_ids": [],
        "conditions": {
            "course_id": sorted(set(course_ids)),
            "fee_status": ["INTL"],
            "new_entrant": [True],
            "academic_conditions_met": [True],
            "offer_received": [True],
            "applied_by_deadline": [True],
            "external_funding": [False],
        },
        "application_deadline": "2027-08-01",
        "scope_note": (
            "First compared study year only. Restricted to reviewed "
            "Humanities research courses starting in 2027/28."
        ),
        "combination_note": (
            "Other tuition awards require separately verified combination "
            "rules. Accommodation benefits are excluded."
        ),
        "published_scholarship_table": {
            "gross": "20270.00",
            "award": "3000.00",
            "net": "17270.00",
        },
    }


class Command(BaseCommand):
    help = (
        "Reconcile Welsh 2027/28 scholarship eligibility and record "
        "Education January 2027 scholarship review status."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            with requests.Session() as session:
                session.headers["User-Agent"] = (
                    "TuitionCompareSourceInspector/1.0"
                )
                retry = Retry(
                    total=3,
                    connect=3,
                    read=3,
                    status=3,
                    backoff_factor=1,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET"],
                    respect_retry_after_header=True,
                )
                session.mount(
                    "https://",
                    HTTPAdapter(max_retries=retry),
                )

                fee_page, fee_evidence = download(session, FEE_URL)
                award_page, award_evidence = download(
                    session,
                    SCHOLARSHIP_URL,
                )
                published_row = verify_award_page(award_page)

                course_evidence = {}

                for course_id, config in COURSES.items():
                    page, evidence = download(session, config["url"])
                    verify_course(page, config)
                    verify_fee_table(
                        fee_page,
                        config["academic_year"],
                        config["amount"],
                    )
                    course_evidence[course_id] = evidence

        except (requests.RequestException, ValueError) as exc:
            raise CommandError(
                f"Source verification stopped: {exc}. "
                "No database records changed."
            ) from exc

        with transaction.atomic():
            if options["apply"]:
                University.objects.select_for_update().get(
                    university_id="UK002"
                )

            records = {}

            for course_id, config in COURSES.items():
                courses = Course.objects.filter(
                    course_id=course_id,
                    university_id="UK002",
                    is_active=True,
                )
                if options["apply"]:
                    courses = courses.select_for_update()

                try:
                    course = courses.get()
                except Course.DoesNotExist as exc:
                    raise CommandError(
                        f"{course_id}: active course missing."
                    ) from exc

                if (
                    course.title != config["title"]
                    or course.level != "PG"
                    or course.duration != config["duration"]
                    or course.start_month != config["month"]
                    or course.start_year != config["year"]
                    or course.course_url.rstrip("/")
                    != config["url"].rstrip("/")
                ):
                    raise CommandError(
                        f"{course_id}: corrected route does not match."
                    )

                tuitions = Tuition.objects.filter(
                    course=course,
                    residency="INTL",
                )
                if options["apply"]:
                    tuitions = tuitions.select_for_update()
                tuitions = list(tuitions)

                if (
                    len(tuitions) != 1
                    or tuitions[0].currency != "GBP"
                    or Decimal(str(tuitions[0].tuition_fee))
                    != Decimal(config["amount"])
                ):
                    raise CommandError(
                        f"{course_id}: expected verified tuition missing."
                    )

                tuition = tuitions[0]
                sources = TuitionSource.objects.filter(tuition=tuition)
                if options["apply"]:
                    sources = sources.select_for_update()

                try:
                    source = sources.get()
                except TuitionSource.DoesNotExist as exc:
                    raise CommandError(
                        f"{course_id}: fee source missing."
                    ) from exc

                raw = source.raw_fees
                if (
                    not isinstance(raw, dict)
                    or raw.get("verified") is not True
                    or source.academic_year != config["academic_year"]
                    or raw.get("course_kind") != "postgraduate_research"
                    or raw.get("fee_band") != "Humanities"
                ):
                    raise CommandError(
                        f"{course_id}: verified research metadata required."
                    )

                records[course_id] = {
                    "tuition": tuition,
                    "source": source,
                    "before": copy.deepcopy(raw),
                }

            awards = Scholarship.objects.filter(
                university_id="UK002",
                name=AWARD_NAME,
                level="PG",
            )
            if options["apply"]:
                awards = awards.select_for_update()
            awards = list(awards)

            if len(awards) > 1:
                raise CommandError("Duplicate Humanities research awards.")

            award = awards[0] if awards else None
            rule = None

            if award is not None:
                rules = ScholarshipRule.objects.filter(scholarship=award)
                if options["apply"]:
                    rules = rules.select_for_update()
                rule = rules.first()

                if rule is None or not isinstance(rule.rules, dict):
                    raise CommandError("Existing research rule needs review.")

            existing_ids = []
            if rule is not None:
                existing_ids = rule.rules.get(
                    "conditions", {}
                ).get("course_id", [])

                if (
                    not isinstance(existing_ids, list)
                    or not all(isinstance(item, str) for item in existing_ids)
                ):
                    raise CommandError("Existing research scope is invalid.")

                # Permit only a scope extension of the same reviewed rule.
                expected_existing = desired_rules(existing_ids)
                if (
                    rule.rules != expected_existing
                    or rule.verified is not True
                    or rule.source_url != SCHOLARSHIP_URL
                ):
                    raise CommandError(
                        "Existing research rule differs from this reviewed "
                        "configuration; no overwrite performed."
                    )

            # Prevent a second Global Citizens rule for the same course/year.
            other_rules = ScholarshipRule.objects.filter(
                scholarship__university_id="UK002",
                verified=True,
            )
            if award is not None:
                other_rules = other_rules.exclude(scholarship=award)

            for other in other_rules:
                data = other.rules
                if (
                    isinstance(data, dict)
                    and data.get("academic_year") == "2027/28"
                    and other.source_url.rstrip("/")
                    == SCHOLARSHIP_URL.rstrip("/")
                    and WELSH_ID in data.get(
                        "conditions", {}
                    ).get("course_id", [])
                ):
                    raise CommandError(
                        "Welsh already appears in another verified "
                        "Global Citizens rule. Review that scope first."
                    )

            new_rules = desired_rules([*existing_ids, WELSH_ID])

            award_values = {
                "award_type": "Tuition Reduction",
                "award_value": "3000.00",
                "currency": "GBP",
                "deadline": None,
                "eligibility": (
                    "New international students on reviewed Humanities "
                    "research courses for 2027/28."
                ),
                "requirements": (
                    "Offer received; academic entry requirements met; "
                    "course application by 1 August 2027; no external "
                    "scholarship, funding or bursary."
                ),
                "description": (
                    "Published annual tuition award: GBP 3000.00. "
                    "First study year modelled. Eligibility must be confirmed."
                ),
                "url": SCHOLARSHIP_URL,
            }

            before_award = None if award is None else {
                key: getattr(award, key)
                for key in award_values
            }
            if before_award and before_award["deadline"] is not None:
                before_award["deadline"] = (
                    before_award["deadline"].isoformat()
                )

            before_rule = None if rule is None else {
                "verified": rule.verified,
                "source_url": rule.source_url,
                "rules": copy.deepcopy(rule.rules),
            }
            after_rule = {
                "verified": True,
                "source_url": SCHOLARSHIP_URL,
                "rules": new_rules,
            }

            for course_id, record in records.items():
                updated = copy.deepcopy(record["before"])

                if course_id == WELSH_ID:
                    status = "verified_rule_eligibility_required"
                    note = (
                        "GBP 3000.00 Global Citizens tuition award if "
                        "eligible; net tuition GBP 17270.00 for the first "
                        "compared study year."
                    )
                    review = {
                        "status": status,
                        "award_name": AWARD_NAME,
                        "source_url": SCHOLARSHIP_URL,
                        "academic_year": "2027/28",
                        "gross": "20270.00",
                        "award": "3000.00",
                        "net_if_eligible": "17270.00",
                        "study_year": 1,
                    }
                else:
                    status = "applicability_unconfirmed"
                    note = (
                        "No scholarship deduction has been verified for "
                        "this January 2027 intake. Global Citizens applies "
                        "to new entrants from 2027/28 onwards."
                    )
                    review = {
                        "status": status,
                        "academic_year": "2026/27",
                        "global_citizens_applicable": False,
                        "source_url": SCHOLARSHIP_URL,
                        "reason": note,
                    }

                updated["scholarship_status"] = status
                updated["scholarship_review"] = review
                updated["deposit_status"] = "offer_letter_amount_required"

                notes = updated.get("display_notes", {})
                if not isinstance(notes, dict):
                    notes = {}

                notes["scholarship"] = note
                notes["deposit"] = (
                    "Deposit amount is specified in your individual "
                    "offer letter."
                )
                updated["display_notes"] = notes
                record["after"] = updated

            award_changed = (
                before_award != award_values
                or before_rule != after_rule
            )
            metadata_changed = any(
                record["before"] != record["after"]
                for record in records.values()
            )

            now = datetime.now(timezone.utc)
            folder = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
            folder.mkdir(parents=True, exist_ok=True)
            report_path = folder / (
                "welsh-education-awards-"
                + now.strftime("%Y%m%dT%H%M%S%fZ")
                + ".json"
            )

            report_path.write_text(
                json.dumps({
                    "generated_at": now.isoformat(),
                    "mode": (
                        "apply_requested"
                        if options["apply"]
                        else "preview"
                    ),
                    "evidence": {
                        "scholarship": award_evidence,
                        "published_row": published_row,
                        "fees": fee_evidence,
                        "courses": course_evidence,
                    },
                    "award_before": before_award,
                    "award_after": award_values,
                    "rule_before": before_rule,
                    "rule_after": after_rule,
                    "courses": {
                        course_id: {
                            "before": record["before"],
                            "after": record["after"],
                        }
                        for course_id, record in records.items()
                    },
                }, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            self.stdout.write(
                "MPhil Welsh: conditional award GBP 3000.00; "
                "net if eligible GBP 17270.00."
            )
            self.stdout.write(
                "PhD Education January 2027: Global Citizens does not "
                "apply; other scholarship applicability remains pending."
            )
            self.stdout.write(
                "Award rule: "
                + ("update" if award_changed else "unchanged")
            )
            self.stdout.write(
                "Course metadata: "
                + ("update" if metadata_changed else "unchanged")
            )
            self.stdout.write(f"Report: {report_path}")

            if not options["apply"]:
                self.stdout.write(
                    "Preview only. No database records changed."
                )
                return

            if award_changed:
                if award is None:
                    award = Scholarship.objects.create(
                        university_id="UK002",
                        name=AWARD_NAME,
                        level="PG",
                        **award_values,
                    )
                else:
                    for key, value in award_values.items():
                        setattr(award, key, value)
                    award.save(update_fields=list(award_values))

                ScholarshipRule.objects.update_or_create(
                    scholarship=award,
                    defaults=after_rule,
                )

            for course_id, record in records.items():
                raw_changed = record["before"] != record["after"]

                if raw_changed:
                    source = record["source"]
                    source.raw_fees = record["after"]
                    source.save(update_fields=["raw_fees", "checked_at"])

                if raw_changed or (
                    course_id == WELSH_ID and award_changed
                ):
                    before = {"raw_fees": record["before"]}
                    after = {"raw_fees": record["after"]}

                    if course_id == WELSH_ID:
                        before.update({
                            "award": before_award,
                            "rule": before_rule,
                        })
                        after.update({
                            "award": award_values,
                            "rule": after_rule,
                        })

                    TuitionUpdateAudit.objects.create(
                        tuition=record["tuition"],
                        course_identifier=course_id,
                        residency="INTL",
                        before=before,
                        after=after,
                    )

        self.stdout.write(
            self.style.SUCCESS(
                "Scholarship reconciliation saved."
            )
        )
        self.stdout.write(
            "Gross tuition, stored discounts and numeric deposits unchanged."
        )