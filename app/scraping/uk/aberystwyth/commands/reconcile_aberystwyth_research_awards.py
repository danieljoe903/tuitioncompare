import copy
import hashlib
import json
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    DepositPolicy,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)

from app.scraping.uk.aberystwyth.commands.import_aberystwyth_research_fees import COURSES, YEAR
from app.scraping.uk.aberystwyth.commands.reconcile_aberystwyth_awards import download, text


URLS = {
    "global_citizens": (
        "https://www.aber.ac.uk/en/study-with-us/international/"
        "scholarships/global-citizens-scholarship/"
    ),
    "abergrad": (
        "https://www.aber.ac.uk/en/study-with-us/fees/"
        "postgrad/uk/taught/abergrad/"
    ),
    "aberdoc": (
        "https://www.aber.ac.uk/en/study-with-us/fees/"
        "postgrad/uk/research/aberdoc/"
    ),
    "deposit": (
        "https://www.aber.ac.uk/en/study-with-us/"
        "international/cas-and-deposit-information/"
    ),
}


class Command(BaseCommand):
    help = (
        "Review scholarships for ten January 2027 research courses "
        "and save the applicable offer-letter deposit policy."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        directory = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aberystwyth"
            / f"research-awards-{stamp}"
        )
        directory.mkdir(parents=True, exist_ok=True)

        evidence = {}
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

                for name, url in URLS.items():
                    self.stdout.write(f"Reading: {url}")
                    html, main = download(session, url)
                    content = text(main)

                    (directory / f"{name}.html").write_bytes(html)

                    evidence[name] = {
                        "source_url": url,
                        "text": content,
                        "sha256": hashlib.sha256(html).hexdigest(),
                    }

        except (
            requests.RequestException,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            raise CommandError(
                f"Source collection stopped: {exc}. "
                "No database records changed."
            ) from exc

        global_text = evidence["global_citizens"]["text"].casefold()
        deposit_text = evidence["deposit"]["text"]
        deposit_lower = deposit_text.casefold()

        if "2027/28 onwards" not in global_text:
            raise CommandError(
                "Global Citizens start-year terms changed; review required."
            )

        if not all(
            phrase in deposit_lower
            for phrase in (
                "offer letter",
                "amount",
                "tuition fee",
                "exempt",
            )
        ):
            raise CommandError(
                "Deposit policy wording changed; review required."
            )

        scholarship_review = {
            "status": "applicability_unconfirmed",
            "academic_year": YEAR,
            "intake": {"month": "January", "year": 2027},
            "automatic_deduction_verified": False,
            "findings": [
                {
                    "name": "Global Citizens Scholarship",
                    "status": "outside_published_start_year",
                    "source_url": URLS["global_citizens"],
                    "reason": (
                        "Published eligibility starts with new students "
                        "from 2027/28 onward."
                    ),
                },
                {
                    "name": "AberGrad Scholarship",
                    "status": "requires_cohort_and_eligibility_confirmation",
                    "source_url": URLS["abergrad"],
                    "reason": (
                        "Do not transfer a published 2026 enrolment award "
                        "to January 2027 without confirming applicability "
                        "and the applicant's alumni eligibility."
                    ),
                },
                {
                    "name": "AberDoc Scholarship",
                    "status": "requires_intake_and_award_confirmation",
                    "source_url": URLS["aberdoc"],
                    "reason": (
                        "Published competition refers to September 2026. "
                        "January 2027 applicability and an individual "
                        "award have not been confirmed."
                    ),
                },
            ],
            "note": (
                "This is a review record, not confirmation that no "
                "scholarships exist. No new calculable award is created."
            ),
        }

        deposit_after = {
            "currency": "GBP",
            "rules": {
                "schema_version": 1,
                "kind": "offer_letter",
                "fee_status": "INTL",
                "payment_stage": "before_cas",
                "amount": None,
                "percentage": None,
                "currency": "GBP",
                "amount_requires_individual_confirmation": True,
                "exemptions_require_confirmation": True,
                "reviewed_intake": {
                    "month": "January",
                    "year": 2027,
                },
                "note": (
                    "Where a deposit is required, the amount is specified "
                    "in the applicant's offer letter. It forms part of "
                    "tuition payments. Exemptions require confirmation."
                ),
            },
            "source_url": URLS["deposit"],
            "verified": True,
        }

        proposals = []
        locked_sources = {}

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

            for suffix in COURSES:
                course_id = f"C_UK002_{suffix}"

                records = list(
                    Tuition.objects.select_for_update().filter(
                        course_id=course_id,
                        course__university=university,
                        course__is_active=True,
                        course__start_month="January",
                        course__start_year=2027,
                        residency="INTL",
                    )
                )

                if len(records) != 1:
                    raise CommandError(
                        f"{course_id}: apply the research tuition batch "
                        "first; one international record is required."
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
                    or raw.get("course_kind") != "postgraduate_research"
                ):
                    raise CommandError(
                        f"{course_id}: verified research fee evidence required."
                    )

                before = copy.deepcopy(raw)
                after = copy.deepcopy(raw)
                after["scholarship_status"] = "applicability_unconfirmed"
                after["scholarship_review"] = scholarship_review
                after["deposit_status"] = "offer_letter_amount_required"

                proposals.append({
                    "type": "course_review",
                    "course_id": course_id,
                    "changed": before != after,
                    "before": before,
                    "after": after,
                })
                locked_sources[course_id] = (tuition, source)

            policy = DepositPolicy.objects.select_for_update().filter(
                university=university,
                academic_year=YEAR,
            ).first()

            deposit_before = (
                {key: getattr(policy, key) for key in deposit_after}
                if policy else None
            )

            proposals.append({
                "type": "deposit_policy",
                "changed": deposit_before != deposit_after,
                "before": deposit_before,
                "after": deposit_after,
            })

            report_path = directory / "reconciliation.json"
            report_path.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "university_id": "UK002",
                        "academic_year": YEAR,
                        "mode": "apply" if options["apply"] else "preview",
                        "evidence": evidence,
                        "proposals": proposals,
                        "tuition_prices_changed": False,
                        "stored_discounts_changed": False,
                        "stored_deposit_amounts_changed": False,
                    },
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )

            if options["apply"]:
                for proposal in proposals:
                    if (
                        proposal["type"] != "course_review"
                        or not proposal["changed"]
                    ):
                        continue

                    tuition, source = locked_sources[proposal["course_id"]]
                    source.raw_fees = proposal["after"]
                    source.save(update_fields=["raw_fees"])

                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=proposal["course_id"],
                        residency="INTL",
                        before={"raw_fees": proposal["before"]},
                        after={"raw_fees": proposal["after"]},
                    )

                DepositPolicy.objects.update_or_create(
                    university=university,
                    academic_year=YEAR,
                    defaults={
                        **deposit_after,
                        "source_text": deposit_text,
                        "source_sha256": hashlib.sha256(
                            deposit_text.encode("utf-8")
                        ).hexdigest(),
                        "checked_at": now,
                    },
                )

        for proposal in proposals:
            label = proposal.get("course_id", "2026/27 deposit policy")
            self.stdout.write(
                f"{label}: "
                f"{'update' if proposal['changed'] else 'unchanged'}"
            )

        self.stdout.write(f"Report: {report_path}")
        self.stdout.write(
            "Saved research scholarship review and deposit policy."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "No scholarship deduction was newly verified. "
            "Tuition prices and stored discount/deposit amounts unchanged."
        )