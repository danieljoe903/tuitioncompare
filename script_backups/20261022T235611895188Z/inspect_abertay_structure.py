from pathlib import Path
from bs4 import BeautifulSoup

# The distinction: headings tell us what information exists; 
# their surrounding HTML tells us how to extract its value.
html_file = Path(__file__).resolve().parent / "abertay_course.html"

soup = BeautifulSoup(
    html_file.read_text(encoding="utf-8",),
    "html.parser"
)
targets = {
    "Start Date",
    "Duration",
    "Award Title",
    "UCAS Code",
    "Annual Tuition Fees",
}
print("--- Detail heading structures ---")

for heading in soup.find_all(["h3", "h4"]):
    label = heading.get_text(" ", strip=True)

    if label not in targets:
        continue
    print(f"\nHeading: {label}")
    print(str(heading.parent)[:2500])

print("\n--- Raw first table ---")

table = soup.find_all("table")
if table is not None:
    print(str(table)[:6000])
else:
    print("No table found.")
