import copy
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    Scholarship,
    ScholarshipRule,
    Tuition,
    TuitionSource,
    University,
)

from app.scraping.uk.aberystwyth.commands.reconcile_aberystwyth_awards import YEAR, SCHOLARSHIP_URL, amount, download, parse_scholarships, rule_data, text


GROUPS = [
    {
        "name": f"Global Citizens Scholarship — Humanities UG ({YEAR})",
        "level": "UG",
        "band": "Humanities",
        "courses": ["C_ABE002_01"],
        "gross": "20400.00",
        "award": "3000.00",
        "net": "17400.00",
        "verified": True,
        "must_exist": True,
    },
    {
        "name": f"Global Citizens Scholarship — Sciences UG ({YEAR})",
        "level": "UG",
        "band": "Sciences",
        "courses": ["C_UK002_54"],
        "gross": "23255.00",
        "award": "4500.00",
        "net": None,
        "verified": False,
        "must_exist": True,
    },
    {
        "name": f"Global Citizens Scholarship — MBA PG ({YEAR})",
        "level": "PG",
        "band": "MBA",
        "courses": [
            "C_UK002_23",
            "C_UK002_45",
            "C_UK002_46",
            "C_UK002_64",
        ],
        "gross": "23310.00",
        "award": "3000.00",
        "net": "20310.00",
        "verified": True,
        "must_exist": False,
    },
]


def parse_mba(main):
    matches = []

    for table in main.find_all("table"):
        rows = [
            [text(cell) for cell in row.find_all(["th", "td"])]
            for row in table.find_all("tr")
        ]

        if not rows or not any(
            YEAR in cell for row in rows[:2] for cell in row
        ):
            continue

        # Require the taught-master's humanities row in the same table.
        humanities = [
            row for row in rows
            if len(row) >= 4
            and row[0].strip().casefold() == "humanities"
        ]
        mba = [
            row for row in rows
            if len(row) >= 4
            and row[0].strip().casefold() == "mba"
        ]

        if len(humanities) != 1 or len(mba) != 1:
            continue

        if amount(humanities[0][1]) != Decimal("22115.00"):
            continue

        values = tuple(str(amount(cell)) for cell in mba[0][1:4])

        if values == ("23310.00", "3000.00", "20310.00"):
            matches.append({
                "gross": values[0],
                "award": values[1],
                "net": values[2],
            })

    if len(matches) != 1:
        raise ValueError(
            "Expected one reviewed 2027/28 MBA scholarship row."
        )

    return matches[0]


class Command(BaseCommand):
    help = (
        "Extend scholarship scope to four batch-three courses. "
        "Science undergraduate deduction remains unverified."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        try:
            with requests.Session() as session:
                session.headers["User-Agent"] = (
                    "TuitionCompareSourceInspector/1.0"
                )
                session.mount(
                    "https://",
                    HTTPAdapter(max_retries=retry),
                )

                html, main = download(session, SCHOLARSHIP_URL)
                published, _content = parse_scholarships(main)
                mba_values = parse_mba(main)

        except (
            requests.RequestException,
            UnicodeDecodeError,
            ValueError,
            ArithmeticError,
        ) as exc:
            raise CommandError(
                f"Scholarship verification stopped: {exc}"
            ) from exc

        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        directory = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aberystwyth"
            / f"batch-three-awards-{stamp}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "scholarship.html").write_bytes(html)

        proposals = []

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

            for group in GROUPS:
                for course_id in group["courses"]:
                    records = list(
                        Tuition.objects.select_for_update().filter(
                            course_id=course_id,
                            course__university=university,
                            course__is_active=True,
                            course__start_month="September",
                            course__start_year=2027,
                            residency="INTL",
                        )
                    )

                    if len(records) != 1:
                        raise CommandError(
                            f"{course_id}: apply batch-three tuition first."
                        )

                    tuition = records[0]
                    source = (
                        TuitionSource.objects.select_for_update()
                        .filter(tuition=tuition)
                        .first()
                    )
                    raw = source.raw_fees if source else None

                    if (
                        source is None
                        or source.academic_year != YEAR
                        or source.fee_basis != "annual_tuition"
                        or not isinstance(raw, dict)
                        or raw.get("verified") is not True
                        or raw.get("fee_band") != group["band"]
                        or tuition.currency != "GBP"
                        or Decimal(str(tuition.tuition_fee))
                        != Decimal(group["gross"])
                    ):
                        raise CommandError(
                            f"{course_id}: reviewed fee evidence mismatch."
                        )

                matches = list(
                    Scholarship.objects.select_for_update().filter(
                        university=university,
                        name=group["name"],
                        level=group["level"],
                    )
                )

                if len(matches) > 1:
                    raise CommandError(
                        f"Duplicate scholarship: {group['name']}"
                    )

                award = matches[0] if matches else None
                existing_rule = (
                    ScholarshipRule.objects.select_for_update()
                    .filter(scholarship=award)
                    .first()
                    if award else None
                )

                if group["must_exist"] and (
                    award is None or existing_rule is None
                ):
                    raise CommandError(
                        "Apply the main Aberystwyth award reconciliation first."
                    )

                if group["band"] == "MBA":
                    values = mba_values
                else:
                    values = published[("UG", group["band"])]

                if existing_rule:
                    if (
                        existing_rule.source_url != SCHOLARSHIP_URL
                        or existing_rule.rules.get("academic_year") != YEAR
                        or existing_rule.rules.get("kind") != "fixed"
                        or Decimal(str(existing_rule.rules.get("value")))
                        != Decimal(group["award"])
                        or existing_rule.verified is not group["verified"]
                    ):
                        raise CommandError(
                            f"{group['name']}: existing rule changed."
                        )

                    rules = copy.deepcopy(existing_rule.rules)
                    existing_ids = rules.get(
                        "conditions", {}
                    ).get("course_id")

                    if not isinstance(existing_ids, list) or not all(
                        isinstance(value, str) for value in existing_ids
                    ):
                        raise CommandError(
                            f"{group['name']}: invalid existing course scope."
                        )

                    rules["conditions"]["course_id"] = sorted(
                        set(existing_ids) | set(group["courses"])
                    )
                else:
                    rules = rule_data(
                        group["courses"],
                        values,
                        group["verified"],
                    )

                rules["published_scholarship_table"] = values

                fields = {
                    "award_type": "Tuition Reduction",
                    "award_value": group["award"],
                    "currency": "GBP",
                    "deadline": None,
                    "eligibility": (
                        award.eligibility if award else
                        "Eligible new international students on the "
                        "reviewed campus MBA courses for 2027/28."
                    ),
                    "requirements": (
                        award.requirements if award else
                        "Offer received; academic entry requirements met; "
                        "course application by 1 August 2027; no external "
                        "scholarship, funding or bursary."
                    ),
                    "description": (
                        award.description if award else
                        "GBP 3,000 annual tuition reduction. First compared "
                        "study year modelled; eligibility must be confirmed."
                    ),
                    "url": SCHOLARSHIP_URL,
                }

                before = {
                    "award": (
                        {key: getattr(award, key) for key in fields}
                        if award else None
                    ),
                    "rule": (
                        {
                            "verified": existing_rule.verified,
                            "source_url": existing_rule.source_url,
                            "rules": existing_rule.rules,
                        }
                        if existing_rule else None
                    ),
                }

                after = {
                    "award": fields,
                    "rule": {
                        "verified": group["verified"],
                        "source_url": SCHOLARSHIP_URL,
                        "rules": rules,
                    },
                }

                proposals.append({
                    "name": group["name"],
                    "level": group["level"],
                    "batch_course_ids": group["courses"],
                    "scope_count": len(rules["conditions"]["course_id"]),
                    "verified": group["verified"],
                    "changed": before != after,
                    "before": before,
                    "after": after,
                })

            report_path = directory / "reconciliation.json"
            report_path.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "university_id": "UK002",
                        "academic_year": YEAR,
                        "mode": "apply" if options["apply"] else "preview",
                        "source_url": SCHOLARSHIP_URL,
                        "source_sha256": hashlib.sha256(html).hexdigest(),
                        "proposals": proposals,
                        "tuition_prices_changed": False,
                        "stored_discounts_changed": False,
                        "deposit_amounts_changed": False,
                    },
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )

            if options["apply"]:
                for proposal in proposals:
                    if not proposal["changed"]:
                        continue

                    award, _created = Scholarship.objects.update_or_create(
                        university=university,
                        name=proposal["name"],
                        level=proposal["level"],
                        defaults=proposal["after"]["award"],
                    )
                    ScholarshipRule.objects.update_or_create(
                        scholarship=award,
                        defaults={
                            **proposal["after"]["rule"],
                            "reviewed_at": now,
                        },
                    )

        for proposal in proposals:
            self.stdout.write(
                f"{proposal['name']}: "
                f"{proposal['scope_count']} total courses; "
                f"{'verified' if proposal['verified'] else 'REVIEW REQUIRED'}"
                f" — {'update' if proposal['changed'] else 'unchanged'}"
            )

        self.stdout.write(f"Report: {report_path}")
        self.stdout.write(
            "Scholarship rules saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Tuition prices, stored discounts and deposits unchanged."
        )