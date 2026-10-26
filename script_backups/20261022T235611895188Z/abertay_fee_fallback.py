import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


FEE_SOURCE_URL = (
    "https://www.abertay.ac.uk/study-apply/"
    "money-fees-and-funding/tuition-fees-2026-2027/"
)

# Only these inspected course pages may use this fallback.
SUPPORTED_PATHS = {
    "/course-search/undergraduate/criminology-and-policing/":
        "BA (Hons) Criminology and Policing",
    "/course-search/undergraduate/psychology-and-counselling/":
        "BSc (Hons) Psychology and Counselling",
}


def clean_text(element):
    """Extract text and normalize whitespace."""
    return " ".join(element.get_text(" ", strip=True).split())


def parse_missing_course_fee(
    course_html,
    course_url,
    academic_year,
    central_fee_html,
):
    """Extract course details and a reviewed central annual fee."""

    if academic_year != "2026/27":
        raise ValueError(
            "This fallback has only been reviewed for 2026/27."
        )

    parsed_url = urlsplit(course_url)
    expected_title = SUPPORTED_PATHS.get(parsed_url.path)

    if (
        parsed_url.scheme != "https"
        or parsed_url.netloc != "www.abertay.ac.uk"
        or not expected_title
    ):
        raise ValueError(
            "Course is not approved for this fallback."
        )

    # Extract identity from the course page.
    course_soup = BeautifulSoup(course_html, "html.parser")
    heading = course_soup.find("h1")

    if heading is None:
        raise ValueError("Course title is missing.")

    title = clean_text(heading)

    if title != expected_title:
        raise ValueError(
            "Course title changed; review fallback identity."
        )

    details = {}

    for item in course_soup.select("div.e-item"):
        label = item.find("h4")
        value = item.find("span", recursive=False)

        if label is not None and value is not None:
            details[clean_text(label)] = clean_text(value)

    for label in ("Duration", "Award Title", "UCAS Code"):
        if not details.get(label):
            raise ValueError(
                f"Missing course detail: {label}"
            )

    # Verify the central fee page's academic year.
    fee_soup = BeautifulSoup(central_fee_html, "html.parser")
    fee_heading = fee_soup.find("h1")

    if (
        fee_heading is None
        or "2026-2027" not in clean_text(fee_heading)
    ):
        raise ValueError(
            "Unexpected central fee-page academic year."
        )

    section_title = (
        "Undergraduate Tuition Fees - "
        "International Students 2026/27"
    )

    sections = fee_soup.find_all(
        lambda tag: tag.name in ("h2", "h3", "h4")
        and clean_text(tag) == section_title
    )

    if len(sections) != 1:
        raise ValueError(
            "Expected one international undergraduate section."
        )

    section = sections[0]

    # Search within the international-fees accordion.
    container = section.find_parent("div", class_="e-item")

    if container is None:
        raise ValueError(
            "International fee accordion was not found."
        )

    tables = []
    inspected_headers = []

    for candidate_table in container.find_all("table"):
        first_row = candidate_table.find("tr")

        if first_row is None:
            continue

        headers = [
            clean_text(cell)
            for cell in first_row.find_all(["th", "td"])
        ]

        inspected_headers.append(headers)

        if (
            len(headers) == 2
            and headers[0].casefold()
            == "undergraduate degree courses"
            and "tuition fees per year" in headers[1].casefold()
        ):
            tables.append(candidate_table)

    if len(tables) != 1:
        raise ValueError(
            "Expected one central undergraduate fee table; "
            f"found {len(tables)}. "
            f"Inspected headers: {inspected_headers!r}"
        )

    table = tables[0]

    # Stop if either course becomes a named fee exception.
    for link in table.find_all("a", href=True):
        linked_url = urljoin(FEE_SOURCE_URL, link["href"])
        linked_path = urlsplit(linked_url).path

        if linked_path.rstrip("/") == parsed_url.path.rstrip("/"):
            raise ValueError(
                "Course now has a named fee; review the fallback."
            )

    # Find the published default bachelor's fee.
    default_label = (
        "All other Undergraduate Bachelors Degree Programmes"
    )
    default_values = []

    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"])

        if len(cells) != 2:
            continue

        if clean_text(cells[0]) == default_label:
            default_values.append(clean_text(cells[1]))

    if len(default_values) != 1:
        raise ValueError(
            "Default undergraduate fee is ambiguous."
        )

    raw_fee = default_values[0]

    match = re.fullmatch(
        r"£\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d{2})?",
        raw_fee,
    )

    if match is None:
        raise ValueError(
            f"Unsupported central fee: {raw_fee!r}"
        )

    amount = (
        match.group(1).replace(",", "")
        + (match.group(2) or ".00")
    )

    return {
        "title": title,
        "start_month": details.get("Start Date"),
        "duration": details["Duration"],
        "award_title": details["Award Title"],
        "ucas_code": details["UCAS Code"],
        "academic_year": academic_year,
        "fee_status": "International",
        "currency": "GBP",
        "raw_fee": raw_fee,
        "annual_tuition": amount,
        "footnote_marker": "",
        "fee_source_url": FEE_SOURCE_URL,
        "fee_method": "reviewed_central_default",
        "fee_rule": default_label,
        "fee_categories": [
        {
        "source_fee_category": "International",
        "academic_year": academic_year,
        "currency": "GBP",
        "raw_annual_fee": raw_fee,
        "annual_tuition": amount,
        "footnote_marker": "",
        "annotation": "",
        "amount_requires_review": False,
        }
    ],
    }