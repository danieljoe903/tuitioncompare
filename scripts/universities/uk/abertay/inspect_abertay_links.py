

# Locate the project root after this script has been relocated.
import sys as _script_sys
from pathlib import Path as _ScriptPath

_script_locations = []
if "__file__" in globals():
    _script_locations.extend(_ScriptPath(__file__).resolve().parents)
_script_locations.extend([
    _ScriptPath.cwd(),
    *_ScriptPath.cwd().parents,
])

_SCRIPT_ROOT = next(
    (
        location for location in _script_locations
        if (location / "manage.py").is_file()
        and (location / "app").is_dir()
    ),
    None,
)
if _SCRIPT_ROOT is None:
    raise RuntimeError(
        "Cannot locate the Academy project. Run from its main folder."
    )

if str(_SCRIPT_ROOT) not in _script_sys.path:
    _script_sys.path.insert(0, str(_SCRIPT_ROOT))

# Inspect its HTML and identify course links.

from pathlib import Path
from urllib.parse import urljoin, parse_qs,urlsplit
from bs4 import BeautifulSoup

BASE_URL = "https://www.abertay.ac.uk"
html_file = _SCRIPT_ROOT / "abertay_catalogue.html"

html = html_file.read_text(encoding="utf-8")
soup = BeautifulSoup(html,"html.parser") #parses the saved HTML.

courses = {}
for link in soup.select("a[href]"):
    href = link.get("href", "")
    absolute_url = urljoin(BASE_URL, href)
    parsed = urlsplit(absolute_url)

    # Extract the destination from Abertay's search redirect.
    if(
        parsed.hostname == "search.abertay.ac.uk"
        and parsed.path == "/s/redirect"
    ):
        query = parse_qs(parsed.query)
        destinations = query.get("url", [])

        if len(destinations) != 1:
            continue

        course_url = destinations[0]
    else:
        course_url = absolute_url

    # Validate the decoded destination.
    destination = urlsplit(course_url)
    if(
        destination.scheme != "https"
        or destination.hostname != "www.abertay.ac.uk"
        or not destination.path.startswith("/course-search/undergraduate/")
        or destination.path == "/course-search/undergraduate/"
    ):

        continue

    title = link.get_text(" ", strip=True)
     
    # The catalogue repeats links as "View course".
    if not title or title.lower() == "view course":
        continue
    courses.setdefault(course_url, title)

for course_url, title in courses.items():
    print("\nTitle:", title)
    print("URL:", course_url)

print("\nUnique candidate courses:", len(courses))
print("\nPagination links:")

for link in soup.select("a[href]"):
    href = link.get("href", "")
    absolute_url = urljoin(BASE_URL, href)
    parsed = urlsplit(absolute_url)
    query = parse_qs(parsed.query)

    if(
        parsed.hostname == "www.abertay.ac.uk"
        and parsed.path.rstrip("/") == "/course-search"
        and "pageNumber" in query
    ):

        print(link.get_text(" ", strip=True), "=>", absolute_url)







# print("Page title:", soup.title.get_text(strip=True) if soup.title else "Missing")
# links = soup.select("a[href]") #selects anchor elements that have a link destination.
# print(f"Total links: {len(links)}")

# seen_url = set() #removes repeated URLs.
# for link in links:
#     href = link.get("href", "")
#     url = urljoin(BASE_URL, href)
#     if "/course-search/undergraduate/" not in url:
#         continue
#     if url in seen_url:
#         continue

#     seen_url.add(url)

#     print("\nTitle:", link.get_text(" ", strip=True))
#     print("URL", url)
#     print("Link classes:", link.get("class", []))
#     print("Parent tag:", link.parent.name)
#     print("Parent classes:", link.parent.get("class", []))
# print("\nUnique candidate course links:",len(seen_url))

# print("\n--- Links containing 'course' ---")

# for link in links:
#     href = link.get("href", "")
#     if "course" in href.lower():
#         print(link.get_text(" ", strip=True), "=>", href)

# print("\n--- External JavaScript files ---")

# for script in soup.select("script[src]"):
#     print(urljoin(BASE_URL, script["src"]))

# print("\n--- Possible result containers ---")

# for element in soup.find_all("div", "section", "form"):
#     element_id = element.get("id", "")
#     classes = " ".join(element.get("class", []))
#     identifiers = f"{element_id} {classes}".lower()

#     if any(word in identifiers for word in("course", "search", "result")):
#         print({
#             "tag": element.name,
#             "id": element_id,
#             "class": classes
#         })