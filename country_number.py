from django.core.management.base import BaseCommand

from app.models import University,Course


# class Command(BaseCommand):
#     help = "List UK universities and show their total."

#     def handle(self, *args, **options):
#         universities = (
#             University.objects.filter(country="UK")
#             .order_by("name")
#         )

#         self.stdout.write("UK universities:")

#         for university in universities:
#             self.stdout.write(
#                 f"{university.pk}: {university.name}"
#             )

#         self.stdout.write(
#             f"\nNumber of UK universities: {universities.count()}"
#         )



# university = University.objects.filter(
#         country="UK"
#     ).order_by("name")

# for uni in university:
#     print(f"pk: {uni.pk}: - {uni.name}")
# print(f"\ntotal universities: {university.count()}")

course = Course.objects.filter(
    university__university_id='UK007'
).values("university__name").order_by("course_id")

n = []
for c in course:
    name = c["university__name"]
n.append(name)
for i in n:
    print(f"Name: {i}")

print(f"Count: {len(course)}")