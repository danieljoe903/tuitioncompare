

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

# This reveals where the course details and fee tables 
# sit in the HTML, without guessing selectors

from urllib.parse import urlsplit,urljoin,parse_qs
from bs4 import BeautifulSoup
from pathlib import Path
import requests

URL = "https://www.abertay.ac.uk/course-search/undergraduate/bsc-accounting-and-finance/"

response = requests.get(
    URL,
    headers={"User-Agent": "TuitionCompareSourceInspector/1.0"},
    timeout=(10, 30)
)
print("Requesting:", repr(URL))
response.raise_for_status()
soup = BeautifulSoup(response.text, "html.parser")
output_file = _SCRIPT_ROOT / "abertay_course.html"
output_file.write_text(
    response.text,
    encoding="utf-8"
)
print(f"Final URL: {response.url}")

print("\n--- Main headings ---")
for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
    print(heading.name, "=>", heading.get_text(" ", strip=True))

print("\n--- Table previews ---")

for number, table in enumerate(soup.find_all("table"), start=1):
    print(f"\nTable number: {number}")
    print(f"IDs: {table.get("id")}")
    print(f"Classes: {table.get("class", [])}")

    for row in table.find_all("tr")[:5]:
        cells = row.find_all("th", "td")
        print([cell.get_text(" ", strip=True) for cell in cells])

print("\nHTML saved to:", output_file)