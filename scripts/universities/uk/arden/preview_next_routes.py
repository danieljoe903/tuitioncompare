"""Preview Arden top-up and online postgraduate evidence. No DB writes."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from django.conf import settings

from app.models import Course, Tuition


ROOT = Path(settings.BASE_DIR)
BASE = ROOT / "logs" / "arden"

ROUTES = {
    "C_ARD004_01": (
        "ba-hons-business-management-top-up",
        "campus",
    ),
    "C_UK004_36": ("msc-business-psychology", "online"),
    "C_UK004_37": ("msc-global-health-management", "online"),
    "C_UK004_38": ("ma-serious-and-violent-crime", "online"),
    "C_UK004_53": ("mba-top-up", "online"),
    "C_UK004_64": ("msc-business-transformation", "online"),
    "C_UK004_65": ("ma-design-management", "online"),
    "C_UK004_66": ("executive-mba-ai", "online"),
    "C_UK004_67": ("executive-mba-finance", "online"),
    "C_UK004_68": ("msc-health-and-care-management-top-up", "online"),
    "C_UK004_69": ("msc-psychology-bps", "online"),
    "C_UK004_70": ("pg-cert-neurodiversity", "online"),
    "C_UK004_71": ("llm", "online"),
    "C_UK004_72": ("serious-and-violent-crime", "online"),
}

CAMPUS_CODES = {
    "bl1680": "London Stratford",
    "bl1666": "Manchester",
}


def latest(pattern, filename):
    folders = [
        path for path in BASE.glob(pattern)
        if path.is_dir() and (path / filename).is_file()
    ]
    if not folders:
        raise ValueError(f"Missing evidence: {pattern}")
    return max(folders, key=lambda path: path.name)


def run():
    course_folder = latest(
        "course-evidence-*", "course-evidence.json"
    )
    fee_folder = latest(
        "dynamic-fees-*", "dynamic-fees.json"
    )

    evidence = json.loads(
        (course_folder / "course-evidence.json").read_text(
            encoding="utf-8"
        )
    )
    fees = json.loads(
        (fee_folder / "dynamic-fees.json").read_text(
            encoding="utf-8"
        )
    )

    if Path(fees["source_evidence"]).name != course_folder.name:
        raise ValueError("Course and fee evidence folders do not match.")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "university_id": "UK004",
        "database_changes": False,
        "mappings_approved": False,
        "course_evidence": str(course_folder),
        "fee_evidence": str(fee_folder),
        "proposals": [],
        "blocked": [],
    }

    for course_id, (slug, mode) in ROUTES.items():
        try:
            course = Course.objects.get(
                university_id="UK004", course_id=course_id
            )
            pages = [
                page for page in evidence["pages"]
                if page["source_url"].rstrip("/").endswith("/" + slug)
            ]
            if len(pages) != 1:
                raise ValueError("Expected one exact source route.")

            page = pages[0]
            html = (
                course_folder / Path(page["html_file"]).name
            ).read_bytes()

            if hashlib.sha256(html).hexdigest() != page["sha256"]:
                raise ValueError("Course HTML checksum mismatch.")

            soup = BeautifulSoup(html, "html.parser")
            field = soup.find(
                "input", attrs={"name": "fees_course_id"}
            )
            if field is None:
                raise ValueError("Missing published course ID.")
            source_id = field.get("value", "")

            selector = soup.find(
                "select",
                attrs={"name": "apply_now_course_study_type"},
            )
            if selector is None:
                raise ValueError("Missing study-option selector.")

            options = []
            for option in selector.find_all("option"):
                match = re.fullmatch(
                    r"c(\d+)\|t([^|]+)\|i(\d{4}-\d{2})\|m=(ft|pt)",
                    option.get("value", ""),
                )
                if not match or match.group(1) != source_id:
                    continue

                location_code = match.group(2)
                if mode == "online":
                    if location_code != "dl1668":
                        continue
                    location = "Online"
                else:
                    if (
                        location_code not in CAMPUS_CODES
                        or match.group(4) != "ft"
                    ):
                        continue
                    location = CAMPUS_CODES[location_code]

                options.append({
                    "location": location,
                    "intake": match.group(3),
                    "attendance": (
                        "full_time" if match.group(4) == "ft"
                        else "part_time"
                    ),
                    "source_option": option["value"],
                })

            if not options:
                raise ValueError("No applicable published study options.")

            captures = []
            for country in ("Nigeria", "Ghana"):
                matches = [
                    response for response in fees["responses"]
                    if response["course_page"] == page["source_url"]
                    and response["country"] == country
                ]
                if len(matches) != 1:
                    raise ValueError(
                        f"Expected one fee capture for {country}."
                    )

                capture = matches[0]
                raw = (
                    fee_folder / capture["raw_response_file"]
                ).read_bytes()

                if (
                    hashlib.sha256(raw).hexdigest()
                    != capture["response_sha256"]
                ):
                    raise ValueError("Fee response checksum mismatch.")

                if (
                    capture["request_parameters"]["course_id"]
                    != source_id
                ):
                    raise ValueError("Fee/course ID mismatch.")

                fragment = json.loads(raw)["data"]
                text = BeautifulSoup(
                    fragment, "html.parser"
                ).get_text(" ", strip=True)

                captures.append({
                    "country": country,
                    "source_url": capture["fee_source_url"],
                    "response_sha256": capture["response_sha256"],
                    "text": text,
                })

            if captures[0]["text"] != captures[1]["text"]:
                raise ValueError("Country fee responses differ.")

            text = captures[0]["text"]

            if mode == "online":
                amounts = re.findall(
                    r"Studying Online Fee Total fee:\s*£([\d,]+(?:\.\d{2})?)",
                    text,
                )
                if len(amounts) != 1:
                    raise ValueError(
                        "Expected one explicit online total programme fee."
                    )
                price = amounts[0].replace(",", "")
                fee_basis = "total_programme_fee"
            else:
                for campus in (
                    "Manchester Campus",
                    "London, Stratford Campus",
                ):
                    if (
                        f"{campus} Fee per year: £16,000"
                        not in text
                    ):
                        raise ValueError(
                            f"{campus}: expected annual fee missing."
                        )
                price = "16000"
                fee_basis = "annual_tuition"

            tuitions = list(Tuition.objects.filter(
                course_id=course_id, residency="INTL"
            ))
            if len(tuitions) != 1 or tuitions[0].currency != "GBP":
                raise ValueError(
                    "Expected one GBP international tuition record."
                )

            proposal = {
                "course_id": course_id,
                "stored_title": course.title,
                "stored_location": course.location,
                "stored_duration": course.duration,
                "stored_tuition": str(tuitions[0].tuition_fee),
                "source_heading": page["heading"],
                "source_course_id": source_id,
                "source_url": page["source_url"],
                "proposed_mode": mode,
                "published_amount": price,
                "currency": "GBP",
                "fee_basis": fee_basis,
                "study_options": options,
                "country_evidence": captures,
                "scholarship_status": "applicability_requires_review",
                "deposit_status": (
                    "campus_deposit_policy_not_applicable"
                    if mode == "online"
                    else "individual_application_advice_required"
                ),
                "review_notes": (
                    [
                        "Explicit correction from the stored campus/mixed mode to the collected online route.",
                        "Total programme fee must not be displayed as annual tuition.",
                        "Do not apply UK campus offers to online study.",
                        "Academic-year applicability requires review.",
                    ]
                    if mode == "online" else [
                        "International campus comparison restricted to Manchester and London Stratford.",
                        "Manchester combined-offer arithmetic requires review.",
                    ]
                ),
            }
            report["proposals"].append(proposal)

            print(f"\n{course_id}: {course.title}")
            print(f"  Stored location: {course.location}")
            print(f"  Proposed mode: {mode}")
            print(f"  Published: GBP {price}; {fee_basis}")
            for option in options:
                print(
                    f"  {option['location']}: {option['intake']}; "
                    f"{option['attendance']}"
                )

        except (ValueError, Course.DoesNotExist, KeyError) as exc:
            report["blocked"].append({
                "course_id": course_id,
                "reason": str(exc),
            })
            print(f"\n{course_id}: BLOCKED — {exc}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = BASE / f"next-route-preview-{stamp}.json"
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\nProposals:", len(report["proposals"]))
    print("Blocked:", len(report["blocked"]))
    print("Report:", output)
    print("Preview only. No database records changed.")


run()