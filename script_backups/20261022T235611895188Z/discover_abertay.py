from urllib.parse import urlsplit,urljoin,parse_qs
from bs4 import BeautifulSoup
import requests
from pathlib import Path
import json

# collects Abertay’s undergraduate course titles 
# and official page URLs across all catalogue pages.
# It follows pagination, removes duplicate links and
# stops repeated-page loops


BASE_URL = "https://www.abertay.ac.uk"
START_URL = BASE_URL + "/course-search/?studyLevel=Undergraduate"
MAX_PAGES = 20 #Stops an unexpected endless traversal.

#Reuses the link-decoding logic you tested.
def extract_courses(soup):

    courses = {}

    for link in soup.select("a[href]"):
        url = urljoin(BASE_URL, link["href"])
        parsed = urlsplit(url)

        if(
            parsed.hostname == "search.abertay.ac.uk"
            and parsed.path == "/s/redirect"
        ):

            destinations = parse_qs(parsed.query).get("url", [])

            if len(destinations) != 1:
                continue
            url = destinations[0]

        destination = urlsplit(url)

        if(
            destination.scheme != "https"
            or destination.hostname != "www.abertay.ac.uk"
            or not destination.path.startswith("/course-search/undergraduate/")
            or destination.path == "/course-search/undergraduate/"
        ):
            continue

        title = link.get_text(" ", strip=True)
        if title or title.lower() != "view course":
            courses.setdefault(url, title)

    return courses

#Follows the undergraduate Next link and ignores the reset link.
def find_next_page(soup, current_url):
    for link in soup.select("a[href]"):
        if link.get_text(" ", strip=True).lower() != "next":
            continue

        url = urljoin(current_url, link["href"])
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)

        if(
            parsed.scheme == "https"
            and parsed.hostname == "www.abertay.ac.uk"
            and parsed.path.rstrip("/") == "/course-search"
            and query.get("studyLevel") == ["Undergraduate"]
            and "pageNumber" in query
        ):
            return url

    return None

def main():
    all_courses = {}
    seen_url = set() #Detects repeated pagination URLs
    seen_pages = set()
    next_url = START_URL

#Reuses connections across requests.
    with requests.Session() as session:
        session.headers.update({
            "User-Agent": "TuitionCompareSourceInspector/1.0"
        })

        for page_number in range(1, MAX_PAGES + 1):
            if next_url in seen_url:
                raise RuntimeError("Pagination repeated a URL.")
            seen_url.add(next_url)
            response = session.get(next_url, timeout=(10, 30))
            response.raise_for_status()

            final_url = urlsplit(response.url)

            if(
                final_url.scheme != "https"
                or final_url.hostname != "www.abertay.ac.uk"
                or final_url.path.rstrip("/") != "/course-search"
            ):

                raise RuntimeError(
                    f"Unexpected response URL: {response.url}"
                )
            soup = BeautifulSoup(response.text, "html.parser")
            courses = extract_courses(soup)
            if not courses:
                raise RuntimeError(
                    f"No course links found on page: {page_number}"
                )
            # fingerprint Detects repeated results even 
            # if the URL changes.
            fingerprint = tuple(sorted(courses)) 
            if fingerprint in seen_pages:
                raise RuntimeError("Pagination repeated course results.")
            seen_pages.add(fingerprint)
            all_courses.update(courses)
            print(
                f"Pages: {page_number} {len(courses)}: candidate"
                f"{len(all_courses)}: unique overall"
            )
            next_url = find_next_page(soup,response.url)
            if next_url is None:
                print("No Next link found. Catalogue traversal finished.")
                break
        else:
            raise RuntimeError("Reached the page limit. Inspect pagination.")

    for url, title in all_courses.items():
        print(f"\n{title}\n{url}")

    print(f"\nTotal unique candidate courses: {len(all_courses)}")

    records = [
        {
            "catalogue_title": title,
            "course_url": url,
        }
        for url, title in all_courses.items()
    ]
    output_files = (
        Path(__file__).resolve().parent / 'data/universities/uk/abertay/abertay_course_urls.json'
    )
    output_files.write_text(
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print("Course URLs saved to:", output_files)
if __name__ == "__main__":
    main()