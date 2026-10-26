import copy
import json
import re
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

from app.scraping.uk.aberystwyth.commands.import_aberystwyth_fees import read_json, validate_age


# Official page title, code, page award token, proposed DB title,
# DB level, duration, intake month, intake year, official path.
MATCHES = {
    "C_ABE002_RES_02": (
        "Parasite Control", "C111", "MRES",
        "MRes Parasite Control", "MRES", "1 Year",
        "September", 2027,
        "/postgraduate/parasite-control",
    ),
    "C_ABE002_RES_04": (
        "Modern History", "V140", "MA",
        "MA Modern History", "PG", "1 Year",
        "September", 2027,
        "/postgraduate/modern-history-masters",
    ),
    "C_ABE002_RES_06": (
        "Psychology", "C800", "MPHL",
        "MPhil Psychology", "PG", "1 Year",
        "January", 2027,
        "/postgraduate/mphil-psychology",
    ),
    "C_UK002_10": (
        "International Relations", "142L", "BA",
        "BA (Hons) International Relations", "UG", "3 Years",
        "September", 2027,
        "/undergraduate/142L-international-relations",
    ),
    "C_UK002_41": (
        "Drama and Theatre", "W400", "BA",
        "BA (Hons) Drama and Theatre", "UG", "3 Years",
        "September", 2027,
        "/undergraduate/W400-drama-and-theatre",
    ),
    "C_UK002_60": (
        "Law and Criminology", "MM91", "LLB",
        "LLB (Hons) Law and Criminology", "UG", "3 Years",
        "September", 2027,
        "/undergraduate/MM91-law-and-criminology",
    ),
    "C_UK002_84": (
        "Pure Mathematics / Statistics", "GGC3", "BSC",
        "BSc (Hons) Pure Mathematics / Statistics", "UG", "3 Years",
        "September", 2027,
        "/undergraduate/pure-mathematics-and-statistics-degree",
    ),
}


def course_values(course):
    return {
        "title": course.title,
        "level": course.level,
        "duration": course.duration,
        "start_month": course.start_month,
        "start_year": course.start_year,
        "course_url": course.course_url,
        "ucas_cao_code": course.ucas_cao_code,
    }


class Command(BaseCommand):
    help = (
        "Preview/apply seven proposed official course matches. "
        "Tuition prices, scholarships and deposits are not changed."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--accept-proposed-matches",
            action="store_true",
            help=(
                "Confirm that the displayed current official courses "
                "are the intended replacements for these stored records."
            ),
        )

    def handle(self, *args, **options):
        if options["apply"] and not options["accept_proposed_matches"]:
            raise CommandError(
                "Review the preview first. To accept the proposed course "
                "replacements, use --apply --accept-proposed-matches."
            )

        directory = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
        paths = list(
            directory.glob(
                "remaining-review-*/remaining-course-review.json"
            )
        )

        if not paths:
            raise CommandError("Remaining-course evidence report not found.")

        evidence_path = max(paths, key=lambda path: path.parent.name)
        evidence = read_json(evidence_path)

        if evidence.get("university_id") != "UK002":
            raise CommandError("University evidence mismatch.")

        validate_age(evidence)

        records = {}
        for record in evidence.get("records", []):
            course_id = record.get("course_id")
            if not course_id or course_id in records:
                raise CommandError("Missing or duplicate evidence course ID.")
            records[course_id] = record

        proposals = []
        blocked = []
        locked = {}
        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        output = directory / f"course-matches-{stamp}.json"

        with transaction.atomic():
            university = University.objects.select_for_update().get(
                pk="UK002", country="UK"
            )

            if university.name.strip().casefold() != "aberystwyth university":
                raise CommandError("University identity mismatch.")

            for course_id, match in MATCHES.items():
                (
                    title, code, award, proposed_title, level,
                    duration, month, year, path,
                ) = match

                record = records.get(course_id, {})
                candidates = []

                for candidate in record.get("candidates", []):
                    url = candidate.get("source_url", "")
                    parsed = urlsplit(url)

                    if (
                        parsed.scheme == "https"
                        and parsed.netloc == "courses.aber.ac.uk"
                        and parsed.path.rstrip("/") == path
                        and not parsed.query
                        and not parsed.fragment
                    ):
                        candidates.append(candidate)

                if len(candidates) != 1:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Unique official candidate required.",
                    })
                    continue

                candidate = candidates[0]
                page = candidate.get("evidence", {})
                content = page.get("text", "")
                years = int(duration.split()[0])

                if (
                    page.get("status") != "collected"
                    or page.get("title") != title
                    or not re.search(
                        rf"\b{re.escape(code)}-{re.escape(award)}\b",
                        content,
                        flags=re.IGNORECASE,
                    )
                    or not re.search(
                        rf"Course duration\s+{years}\s+years?\b",
                        content,
                        flags=re.IGNORECASE,
                    )
                    or {"month": month, "year": year}
                    not in page.get("advertised_intakes", [])
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Official award, duration or intake changed.",
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
                        "reason": "Active database course missing.",
                    })
                    continue

                before = course_values(course)
                after = {
                    **before,
                    "title": proposed_title,
                    "level": level,
                    "duration": duration,
                    "start_month": month,
                    "start_year": year,
                    "course_url": candidate["source_url"],
                    "ucas_cao_code": (
                        code if level == "UG" else before["ucas_cao_code"]
                    ),
                }

                stored = record.get("stored", {})
                identity_fields = ("title", "level", "duration")

                if not all(
                    before[field] in (
                        stored.get(field),
                        after[field],
                    )
                    for field in identity_fields
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Database identity changed since collection.",
                    })
                    continue

                # A candidate already assigned elsewhere may represent
                # a duplicate rather than a record to rename.
                duplicate_ids = list(
                    Course.objects.filter(
                        university=university,
                        is_active=True,
                        course_url__in=[
                            candidate["source_url"],
                            candidate["source_url"].rstrip("/"),
                        ],
                    )
                    .exclude(pk=course_id)
                    .values_list("course_id", flat=True)
                )

                if duplicate_ids:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Official route is already mapped elsewhere.",
                        "other_course_ids": duplicate_ids,
                    })
                    continue

                tuitions = list(
                    Tuition.objects.select_for_update().filter(
                        course=course, residency="INTL"
                    )
                )

                if len(tuitions) != 1:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Unique international tuition required.",
                    })
                    continue

                tuition = tuitions[0]
                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                if source is None or not isinstance(source.raw_fees, dict):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Apply the unresolved review metadata first.",
                    })
                    continue

                if source.raw_fees.get("verified") is True:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Fee already verified; separate review required.",
                    })
                    continue

                raw_before = copy.deepcopy(source.raw_fees)
                raw_after = copy.deepcopy(raw_before)
                raw_after["course_identity"] = {
                    "status": "accepted_current_catalogue_match",
                    "official_title": title,
                    "official_code": code,
                    "official_award": award,
                    "source_url": candidate["source_url"],
                    "evidence_generated_at": evidence["generated_at"],
                    "note": (
                        "Accepted replacement for the stored record; "
                        "not a claim of historical course equivalence."
                    ),
                }
                raw_after["fee_verification_status"] = "pending_fee_mapping"
                raw_after["fee_review"] = {
                    "status": "pending",
                    "reason": (
                        "Course identity corrected. Applicable tuition "
                        "must now be mapped to this award and intake."
                    ),
                    "candidate_urls": [candidate["source_url"]],
                }

                changed = before != after or raw_before != raw_after

                proposals.append({
                    "course_id": course_id,
                    "changed": changed,
                    "before": before,
                    "after": after,
                    "metadata_before": raw_before,
                    "metadata_after": raw_after,
                    "changed_fields": [
                        field for field in after
                        if before[field] != after[field]
                    ],
                })
                locked[course_id] = (course, tuition, source)

                self.stdout.write(
                    f"\n{course_id}: "
                    f"{'proposed correction' if changed else 'unchanged'}"
                )
                for field in after:
                    if before[field] != after[field]:
                        self.stdout.write(
                            f"  {field}: {before[field]} -> {after[field]}"
                        )

            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "university_id": "UK002",
                        "mode": "apply" if options["apply"] else "preview",
                        "evidence_report": str(evidence_path),
                        "proposals": proposals,
                        "blocked": blocked,
                        "tuition_prices_changed": False,
                        "scholarship_amounts_changed": False,
                        "deposit_amounts_changed": False,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            if options["apply"] and blocked:
                raise CommandError(
                    f"{len(blocked)} matches need review. "
                    f"No corrections applied. See {output}"
                )

            if options["apply"]:
                for proposal in proposals:
                    if not proposal["changed"]:
                        continue

                    course, tuition, source = locked[proposal["course_id"]]

                    for field, value in proposal["after"].items():
                        setattr(course, field, value)

                    course.save(
                        update_fields=list(proposal["after"]) + ["updated_at"]
                    )

                    source.raw_fees = proposal["metadata_after"]
                    source.save(update_fields=["raw_fees"])

                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=course.pk,
                        residency="INTL",
                        before={
                            "course": proposal["before"],
                            "raw_fees": proposal["metadata_before"],
                        },
                        after={
                            "course": proposal["after"],
                            "raw_fees": proposal["metadata_after"],
                        },
                    )

        self.stdout.write(f"\nProposed matches: {len(proposals)}")
        self.stdout.write(f"Blocked: {len(blocked)}")
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Accepted course corrections and mapping evidence saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Tuition prices, scholarships and deposit amounts unchanged."
        )