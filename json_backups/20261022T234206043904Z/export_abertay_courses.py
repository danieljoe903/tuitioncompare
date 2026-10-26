import json
from pathlib import Path

from django.conf import settings
from app.models import Course

courses = list(
    Course.objects.filter(
        university__name__iexact="Abertay University",
        level__in=["UG", "TOPUP"],
    ).order_by("course_id")
    .values(
        "course_id",
        "title",
        "level",
        "duration",
        "course_url",
        "start_year",
    )
)
output_file = Path(settings.BASE_DIR) / "abertay_database_courses.json"
output_file.write_text(
    json.dumps(courses, indent=2, ensure_ascii=False),
    encoding="utf-8"
)
print("Database courses exported:", len(courses))
print("Saved to:", output_file)