import csv

# Read all course IDs from courses.csv
course_ids = set()
with open('data/courses.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        course_ids.add(row['course_id'])

print(f"Total courses in courses.csv: {len(course_ids)}")

# Check tuition_fees.csv for missing course IDs
missing_courses = set()
with open('data/tuition_fees.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row['course_id'] not in course_ids:
            missing_courses.add(row['course_id'])

if missing_courses:
    print(f"\n❌ Found {len(missing_courses)} course IDs in tuition_fees.csv that don't exist in courses.csv:")
    for course_id in sorted(missing_courses):
        print(f"  - {course_id}")
else:
    print("\n✅ All course IDs in tuition_fees.csv exist in courses.csv")
