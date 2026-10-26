import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management import call_command, get_commands
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import Course, TuitionSource
from app.views import abertay_fee_verification_error


YEAR = "2026/27"

POLICY_URL = (
    "https://www.abertay.ac.uk/study-apply/"
    "money-fees-and-funding/tuition-fees-2026-2027/"
)

IMPORT_COMMANDS = (
    "import_abertay_fees",
    "import_abertay_remaining_fees",
    "reconcile_abertay_early_2026_fees",
    "reconcile_abertay_research_batch",
)

SCHOLARSHIP_COMMAND = "extend_abertay_scholarship_scope"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")

    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


class Command(BaseCommand):
    help = (
        "Refresh Abertay source evidence and preview/apply "
        "reviewed international fees and scholarship scope."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply validated database updates.",
        )
        parser.add_argument(
            "--skip-ug-refresh",
            action="store_true",
            help="Reuse existing undergraduate source files.",
        )
        parser.add_argument(
            "--skip-pg-refresh",
            action="store_true",
            help="Reuse existing postgraduate and policy files.",
        )

    def read_json(self, path):
        try:
            return json.loads(
                path.read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError) as exc:
            raise CommandError(
                f"Cannot read {path.name}: {exc}"
            ) from exc

    def acquire_lock(self, path):
        handle = path.open("a+b")
        handle.seek(0, 2)

        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()

        handle.seek(0)

        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(
                    handle.fileno(),
                    msvcrt.LK_NBLCK,
                    1,
                )
            else:
                import fcntl

                fcntl.flock(
                    handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )

        except OSError as exc:
            handle.close()
            raise CommandError(
                "Another Abertay workflow may already be running."
            ) from exc

        return handle

    def run_script(self, root, filename, *arguments):
        script = root / filename

        if not script.is_file():
            raise CommandError(f"Missing script: {script}")

        self.stdout.write(f"\nRunning {filename}...")
        self.stdout.flush()

        environment = dict(
            os.environ,
            PYTHONUNBUFFERED="1",
            PYTHONIOENCODING="utf-8",
        )

        try:
            result = subprocess.run(
                [sys.executable, str(script), *arguments],
                cwd=root,
                env=environment,
                timeout=1800,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandError(
                f"{filename} exceeded the 30-minute limit."
            ) from exc

        if result.returncode:
            raise CommandError(
                f"{filename} failed with exit code "
                f"{result.returncode}. Imports were not started."
            )

    def validate_undergraduate_sources(self, root):
        catalogue = self.read_json(
            root / "abertay_course_urls.json"
        )
        extraction = self.read_json(
            root / "abertay_courses_preview.json"
        )
        mapping = self.read_json(
            root / "abertay_course_mapping.json"
        )

        if not isinstance(catalogue, list) or not catalogue:
            raise CommandError(
                "Undergraduate catalogue is empty or invalid."
            )

        urls = [
            row.get("course_url")
            if isinstance(row, dict) else None
            for row in catalogue
        ]

        if (
            any(not isinstance(url, str) or not url for url in urls)
            or len(urls) != len(set(urls))
        ):
            raise CommandError(
                "Invalid or duplicate undergraduate URLs."
            )

        records = (
            extraction.get("records")
            if isinstance(extraction, dict) else None
        )

        if (
            not isinstance(records, list)
            or extraction.get("academic_year") != YEAR
            or len(records) != len(catalogue)
            or any(not isinstance(row, dict) for row in records)
        ):
            raise CommandError(
                "Undergraduate extraction coverage is incomplete."
            )

        extracted_urls = [
            row.get("source_url") for row in records
        ]

        if (
            any(
                not isinstance(url, str) or not url
                for url in extracted_urls
            )
            or len(set(extracted_urls)) != len(extracted_urls)
            or set(extracted_urls) != set(urls)
        ):
            raise CommandError(
                "Undergraduate extraction URLs do not match "
                "the catalogue."
            )

        failures = [
            row for row in records
            if row.get("status") != "extracted"
        ]

        if failures:
            for row in failures:
                self.stderr.write(
                    f"{row.get('source_url')}: "
                    f"{row.get('error', 'Extraction incomplete')}"
                )

            raise CommandError(
                f"{len(failures)} undergraduate pages need review."
            )

        entries = (
            mapping.get("mappings")
            if isinstance(mapping, dict) else None
        )

        if (
            not isinstance(entries, list)
            or any(
                not isinstance(entry, dict)
                or not isinstance(entry.get("source_url"), str)
                or not entry["source_url"]
                for entry in entries
            )
        ):
            raise CommandError("Invalid course mapping file.")

        mapped_urls = {
            entry["source_url"] for entry in entries
        }
        missing = mapped_urls - set(urls)

        if missing:
            raise CommandError(
                "Mapped UG pages are missing: "
                + ", ".join(sorted(missing))
            )

        return {
            "extracted_pages": len(records),
            "mapped_source_pages": len(mapped_urls),
            "unmapped_source_pages": len(set(urls) - mapped_urls),
        }

    def validate_postgraduate_sources(
        self,
        preview_path,
        policy_path,
        previous=None,
    ):
        report = self.read_json(preview_path)
        policy = self.read_json(policy_path)

        if (
            not isinstance(report, dict)
            or report.get("academic_year") != YEAR
            or report.get("collection_complete") is not True
            or report.get("catalogue_errors")
        ):
            raise CommandError(
                "Postgraduate collection is incomplete or invalid."
            )

        prefixes = {
            "postgraduate_taught":
                "/course-search/postgraduate-taught/",
            "postgraduate_research":
                "/course-search/postgraduate-research/",
        }

        counts = report.get("catalogue_counts")
        records = report.get("postgraduate")

        if (
            not isinstance(counts, dict)
            or set(counts) != set(prefixes)
            or not isinstance(records, list)
        ):
            raise CommandError(
                "Missing postgraduate catalogue coverage."
            )

        indexed = {}
        actual_counts = {
            kind: 0 for kind in prefixes
        }

        for record in records:
            if not isinstance(record, dict):
                raise CommandError("Invalid PG record.")

            kind = record.get("kind")
            url = record.get("source_url")

            if kind not in prefixes or not isinstance(url, str):
                raise CommandError("Invalid PG source identity.")

            parsed = urlsplit(url)
            prefix = prefixes[kind]

            if (
                parsed.scheme != "https"
                or parsed.netloc != "www.abertay.ac.uk"
                or not parsed.path.startswith(prefix)
                or parsed.path.rstrip("/") == prefix.rstrip("/")
            ):
                raise CommandError(
                    f"Unexpected postgraduate URL: {url}"
                )

            if url in indexed:
                raise CommandError(
                    f"Duplicate postgraduate source: {url}"
                )

            indexed[url] = record
            actual_counts[kind] += 1

            if not record.get("source_title"):
                raise CommandError(
                    f"Missing postgraduate title: {url}"
                )

            if record.get("status") == "extracted":
                data = record.get("data")

                if (
                    not isinstance(data, dict)
                    or data.get("academic_year") != YEAR
                    or data.get("currency") != "GBP"
                ):
                    raise CommandError(
                        f"Invalid extracted PG evidence: {url}"
                    )

            elif not (
                record.get("status") == "needs_review"
                and record.get("error_code") == "missing_fee_table"
            ):
                raise CommandError(
                    f"PG page failed validation: {url}"
                )

        for kind, actual in actual_counts.items():
            if (
                type(counts.get(kind)) is not int
                or counts[kind] <= 0
                or actual != counts[kind]
            ):
                raise CommandError(
                    f"Incomplete catalogue: {kind}"
                )

        # Previously extracted fees cannot silently disappear.
        if isinstance(previous, dict):
            for old in previous.get("postgraduate", []):
                if (
                    not isinstance(old, dict)
                    or old.get("status") != "extracted"
                ):
                    continue

                url = old.get("source_url")
                current = indexed.get(url)

                if (
                    current is None
                    or current.get("status") != "extracted"
                ):
                    raise CommandError(
                        "Previously extracted PG fee evidence "
                        f"is missing: {url}. Imports stopped."
                    )

        if (
            not isinstance(policy, dict)
            or policy.get("source_url") != POLICY_URL
            or policy.get("page_title")
            != "Tuition Fees (2026-2027)"
            or not isinstance(policy.get("sections"), list)
        ):
            raise CommandError("Invalid central fee policy.")

        required_sections = {
            "Undergraduate Tuition Fees - "
            "International Students 2026/27":
                "UNDERGRADUATE DEGREE COURSES",

            "Postgraduate Tuition Fees - "
            "International Students 2026/27":
                "POSTGRADUATE COURSES",
        }

        for heading, first_header in required_sections.items():
            sections = [
                item
                for item in policy["sections"]
                if isinstance(item, dict)
                and item.get("heading") == heading
            ]

            if len(sections) != 1:
                raise CommandError(
                    f"Missing or ambiguous policy section: {heading}"
                )

            tables = [
                table
                for table in sections[0].get("tables", [])
                if isinstance(table, list)
                and table
                and isinstance(table[0], list)
                and table[0]
                and isinstance(table[0][0], str)
                and table[0][0].casefold()
                == first_header.casefold()
            ]

            if len(tables) != 1 or len(tables[0]) < 2:
                raise CommandError(
                    f"Missing or ambiguous policy table: {heading}"
                )

        return {
            "catalogue_counts": actual_counts,
            "extracted_fees": sum(
                row.get("status") == "extracted"
                for row in records
            ),
            "central_fee_evidence_required": sum(
                row.get("error_code") == "missing_fee_table"
                for row in records
            ),
        }

    def promote_source_pair(self, pairs):
        """Install validated files; restore originals on an IO failure."""
        originals = {
            destination: (
                destination.read_bytes()
                if destination.exists() else None
            )
            for _, destination in pairs
        }

        try:
            for staged, destination in pairs:
                staged.replace(destination)
        except OSError:
            for destination, original in originals.items():
                if original is None:
                    destination.unlink(missing_ok=True)
                else:
                    temporary = destination.with_suffix(
                        destination.suffix + ".restore"
                    )
                    temporary.write_bytes(original)
                    temporary.replace(destination)
            raise

    def coverage(self):
        courses = (
            Course.objects.filter(
                university__name__iexact="Abertay University",
                university__country="UK",
                is_active=True,
            )
            .prefetch_related(
                "tuitions",
                "tuitions__source_metadata",
            )
            .order_by("course_id")
        )

        verified = []
        unresolved = []
        years = {}
        restricted = []

        for course in courses:
            row = {
                "course_id": course.course_id,
                "title": course.title,
                "level": course.level,
                "start_month": course.start_month,
                "start_year": course.start_year,
            }

            tuitions = [
                tuition
                for tuition in course.tuitions.all()
                if tuition.residency == "INTL"
            ]

            if len(tuitions) != 1:
                row["reason"] = (
                    f"Expected one INTL tuition; found {len(tuitions)}."
                )
                unresolved.append(row)
                continue

            tuition = tuitions[0]

            try:
                source = tuition.source_metadata
            except TuitionSource.DoesNotExist:
                source = None

            reason = abertay_fee_verification_error(
                tuition, source
            )

            if reason:
                row["reason"] = reason
                unresolved.append(row)
                continue

            row["academic_year"] = source.academic_year
            row["tuition_fee"] = str(tuition.tuition_fee)
            row["source_url"] = source.source_url
            verified.append(row)

            years[source.academic_year] = (
                years.get(source.academic_year, 0) + 1
            )

            if (
                source.raw_fees.get(
                    "international_applications_open"
                ) is False
            ):
                restricted.append(course.course_id)

        return {
            "active_courses": len(verified) + len(unresolved),
            "verified_fees": len(verified),
            "unresolved_fees": len(unresolved),
            "verified_by_academic_year": years,
            "application_restricted_course_ids": restricted,
            "unresolved": unresolved,
        }

    def run_importer(self, name, apply):
        mode = "APPLY" if apply else "PREVIEW"
        self.stdout.write(f"\n[{mode}] {name}")
        self.stdout.flush()

        call_command(
            name,
            apply=apply,
            stdout=self.stdout,
            stderr=self.stderr,
        )

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        log_dir = root / "logs" / "abertay"
        log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        report_path = log_dir / f"workflow-{timestamp}.json"

        report = {
            "mode": "apply" if options["apply"] else "preview",
            "status": "started",
            "database_transaction_committed": False,
            "undergraduate_refreshed": False,
            "postgraduate_refreshed": False,
            "postgraduate_refresh_integrated": True,
            "historical_scholarship_rules_verified": False,
            "deposits_verified_by_this_workflow": False,
            "completed_commands": [],
        }

        lock = self.acquire_lock(log_dir / "workflow.lock")

        try:
            importers = [
                *IMPORT_COMMANDS,
                SCHOLARSHIP_COMMAND,
            ]
            required = [
                *importers,
                "preview_abertay_remaining",
            ]
            available = get_commands()

            missing = [
                name for name in required
                if name not in available
            ]

            if missing:
                raise CommandError(
                    "Missing management commands: "
                    + ", ".join(missing)
                )

            if not options["skip_ug_refresh"]:
                self.run_script(root, "discover_abertay.py")

                catalogue = self.read_json(
                    root / "abertay_course_urls.json"
                )

                if not isinstance(catalogue, list) or not catalogue:
                    raise CommandError(
                        "Discovered undergraduate catalogue is empty."
                    )

                self.run_script(
                    root,
                    "preview_abertay_courses.py",
                    "--limit",
                    str(len(catalogue)),
                    "--academic-year",
                    YEAR,
                )

                report["undergraduate_refreshed"] = True
            else:
                self.stdout.write(
                    "\nUsing existing undergraduate extraction files."
                )

            report["undergraduate_sources"] = (
                self.validate_undergraduate_sources(root)
            )

            preview_path = root / "abertay_remaining_preview.json"
            policy_path = root / "abertay_remaining_policy.json"

            previous = (
                self.read_json(preview_path)
                if preview_path.exists()
                else None
            )

            if not options["skip_pg_refresh"]:
                # Both files are collected and validated in staging
                # before replacing the successful source files.
                with tempfile.TemporaryDirectory(
                    prefix="abertay-stage-",
                    dir=root,
                ) as directory:
                    stage = Path(directory)
                    staged_policy = stage / policy_path.name
                    staged_preview = stage / preview_path.name

                    self.run_script(
                        root,
                        "inspect_abertay_remaining_policy.py",
                        "--output",
                        str(staged_policy),
                    )

                    self.stdout.write(
                        "\nRefreshing postgraduate catalogues..."
                    )
                    self.stdout.flush()

                    call_command(
                        "preview_abertay_remaining",
                        output=str(staged_preview),
                        stdout=self.stdout,
                        stderr=self.stderr,
                    )

                    # Preserve the collected evidence, including
                    # when cross-run validation subsequently fails.
                    evidence_policy = (
                        log_dir / f"policy-{timestamp}.json"
                    )
                    evidence_preview = (
                        log_dir / f"pg-preview-{timestamp}.json"
                    )

                    evidence_policy.write_bytes(
                        staged_policy.read_bytes()
                    )
                    evidence_preview.write_bytes(
                        staged_preview.read_bytes()
                    )

                    report["source_snapshots"] = {
                        "policy": str(evidence_policy),
                        "postgraduate": str(evidence_preview),
                    }

                    report["postgraduate_sources"] = (
                        self.validate_postgraduate_sources(
                            staged_preview,
                            staged_policy,
                            previous=previous,
                        )
                    )

                    self.promote_source_pair([
                        (staged_policy, policy_path),
                        (staged_preview, preview_path),
                    ])

                report["postgraduate_refreshed"] = True
            else:
                self.stdout.write(
                    "\nUsing existing postgraduate and policy files."
                )

                report["postgraduate_sources"] = (
                    self.validate_postgraduate_sources(
                        preview_path,
                        policy_path,
                    )
                )

            report["coverage_before"] = self.coverage()

            self.stdout.write(
                "\nCurrent source evidence validated. Historical "
                "2025/26 fees remain handled by their cohort importer. "
                "Missing course-page tables do not automatically "
                "approve a central-fee match."
            )

            # Every importer must pass preview before apply begins.
            for name in importers:
                self.run_importer(name, apply=False)

            if options["apply"]:
                with transaction.atomic():
                    for name in importers:
                        self.run_importer(name, apply=True)
                        report["completed_commands"].append(name)

                report["database_transaction_committed"] = True
                report["status"] = "applied"
            else:
                report["status"] = "preview_complete"

            report["coverage_after"] = self.coverage()
            write_json(report_path, report)

            coverage = report["coverage_after"]

            self.stdout.write(
                "\nCombined active-course coverage"
            )
            self.stdout.write(
                f"Active courses: {coverage['active_courses']}"
            )
            self.stdout.write(
                f"Verified fees: {coverage['verified_fees']}"
            )
            self.stdout.write(
                f"Unresolved fees: {coverage['unresolved_fees']}"
            )
            self.stdout.write(
                f"Workflow report: {report_path}"
            )

            self.stdout.write(
                "Database workflow committed successfully."
                if options["apply"]
                else "Preview complete. No database updates applied."
            )

        except Exception as exc:
            report["status"] = "failed"
            report["error"] = str(exc)
            write_json(report_path, report)

            if report["database_transaction_committed"]:
                message = (
                    "Database updates committed, but a later "
                    "reporting step failed."
                )
            else:
                message = (
                    "Workflow failed. Any database changes inside "
                    "the combined apply transaction were rolled back."
                )

            raise CommandError(
                f"{message}\n"
                f"Reason: {exc}\n"
                f"Workflow report: {report_path}"
            ) from exc

        finally:
            lock.close() 