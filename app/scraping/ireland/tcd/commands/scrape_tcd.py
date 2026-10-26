import hashlib
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import University, Course


BASE_URL = "https://www.tcd.ie"
SEARCH_URL = urljoin(BASE_URL, "/courses/search/")


class Command(BaseCommand):
    help = "Import or update Trinity College Dublin undergraduate courses."

    def add_arguments(self, parser):
        parser.add_argument(
            "--start-year",
            type=int,
            required=True,
            help="Intake year to assign. Confirm this before importing.",
        )
        parser.add_argument("--max-pages", type=int, default=100)

    def handle(self, *args, **options):
        start_year = options["start_year"]
        max_pages = options["max_pages"]

        if max_pages < 1:
            raise CommandError("--max-pages must be greater than zero.")

        # Reuse the university already imported from CSV.
        matches = University.objects.filter(
            slug="trinity-college-dublin"
        )
        university = matches.first()

        if university is None:
            matches = University.objects.filter(
                name__iexact="Trinity College Dublin"
            )
            if matches.count() > 1:
                raise CommandError(
                    "Multiple Trinity College Dublin records found."
                )
            university = matches.first()

        if university is None:
            university = University.objects.create(
                university_id="TCD",
                name="Trinity College Dublin",
                slug="trinity-college-dublin",
                country="IE",
                city="Dublin",
                overview="Trinity College Dublin",
                website=BASE_URL,
                currency="EUR",
            )

        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        created_count = 0
        updated_count = 0
        skipped_count = 0
        seen_pages = set()
        seen_urls = set()

        with requests.Session() as session:
            session.headers.update({
                "User-Agent": "TuitionCompareCourseImporter/1.0"
            })
            session.mount(
                "https://",
                HTTPAdapter(max_retries=retries),
            )

            for page in range(1, max_pages + 1):
                self.stdout.write(f"Reading page {page}...")

                try:
                    response = session.get(
                        SEARCH_URL,
                        params={
                            "type": "undergraduate",
                            "page": page,
                        },
                        timeout=(10, 30),
                    )
                    response.raise_for_status()
                    self.stdout.write(f"Response URL: {response.url}")
                except requests.RequestException as exc:
                    raise CommandError(
                        f"Page {page} failed: {exc}. "
                        "Earlier pages may already have been saved."
                    ) from exc

                soup = BeautifulSoup(response.text, "html.parser")
                blocks = soup.select(".course-listing-result")

                if not blocks:
                    if page == 1:
                        raise CommandError(
                            "No listings found on page 1. "
                            "Check whether the website structure changed."
                        )
                    break

                records = []

                for block in blocks:
                    link = block.select_one(".course-title a")

                    if link is None:
                        skipped_count += 1
                        continue

                    title = link.get_text(" ", strip=True)
                    href = link.get("href")

                    if not title or not href:
                        skipped_count += 1
                        continue

                    course_url = urljoin(BASE_URL, href)

                    if not course_url.startswith(BASE_URL + "/"):
                        skipped_count += 1
                        continue

                    spans = block.select(".course-details span")
                    duration = (
                        spans[0].get_text(" ", strip=True)
                        if spans else ""
                    )

                    # Reject values exceeding the model's field lengths.
                    if (
                        len(title) > 255
                        or len(duration) > 50
                        or len(course_url) > 200
                    ):
                        self.stderr.write(
                            f"Skipping oversized record: {title}"
                        )
                        skipped_count += 1
                        continue

                    records.append((title, course_url, duration))

                if not records:
                    raise CommandError(
                        f"Page {page} contains listings, but none "
                        "could be parsed. Check the selectors."
                    )

                fingerprint = tuple(
                    sorted(url for _, url, _ in records)
                )

                if fingerprint in seen_pages:
                    self.stdout.write(self.style.WARNING(
                        f"Page {page} repeats an earlier page. "
                        "Stopped to prevent an endless loop."
                    ))
                    break

                seen_pages.add(fingerprint)

                for title, course_url, duration in records:
                    if course_url in seen_urls:
                        continue

                    seen_urls.add(course_url)

                    with transaction.atomic():
                        # Preserve IDs and linked tuition records.
                        existing = list(
                            Course.objects.filter(
                                university=university,
                                course_url=course_url,
                            )[:2]
                        )

                        if not existing:
                            existing = list(
                                Course.objects.filter(
                                    university=university,
                                    title=title,
                                    level="UG",
                                )[:2]
                            )

                        if len(existing) > 1:
                            raise CommandError(
                                f"Ambiguous existing course: {title}"
                            )

                        if existing:
                            course = existing[0]
                            course.title = title
                            course.course_url = course_url
                            course.level = "UG"
                            course.location = "Dublin"

                            # Keep existing duration if parsing found none.
                            if duration:
                                course.duration = duration

                            course.save()
                            updated_count += 1
                        else:
                            # Stable 28-character ID, within max_length=30.
                            digest = hashlib.sha256(
                                course_url.encode("utf-8")
                            ).hexdigest()[:24]
                            course_id = f"TCD-{digest}"

                            if Course.objects.filter(
                                course_id=course_id
                            ).exists():
                                raise CommandError(
                                    f"Generated ID already exists: {course_id}"
                                )

                            Course.objects.create(
                                course_id=course_id,
                                university=university,
                                title=title,
                                level="UG",
                                duration=duration,
                                location="Dublin",
                                start_year=start_year,
                                start_month="",
                                intake_months="",
                                course_url=course_url,
                            )
                            created_count += 1
            else:
                raise CommandError(
                    f"Reached the {max_pages}-page limit. "
                    "Check pagination before increasing it."
                )

        self.stdout.write(self.style.SUCCESS(
            f"Completed: {created_count} created, "
            f"{updated_count} existing records refreshed, "
            f"{skipped_count} skipped."
        ))