import csv
from django.core.management.base import BaseCommand
from app.models import University, Subject, Course, Tuition, Scholarship, UniversityRanking

class Command(BaseCommand):
    help = "Import data from CSV files"

    def handle(self, *args, **kwargs):
        # Import Universities
        with open('data/universities.csv', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                University.objects.update_or_create(
                    university_id=row['university_id'],
                    defaults={
                        'name': row['name'],
                        'slug': row['slug'],
                        'country': row['country'],
                        'city': row['city'],
                        'overview': row['overview'],
                        'estimated_living_cost': row['estimated_living_cost_annual'],
                        'population': row['population'],
                        'admission_requirements': row['admission_requirements'],
                        'acceptance_rate': row['acceptance_rate'],
                        'graduation_rate': row['graduation_rate'],
                        'financial_aid': row['financial_aid'],
                        'website': row['website'],
                        'avg_ug_tuition': row['avg_ug_tuition'],
                        'avg_pg_tuition': row['avg_pg_tuition'],
                        'ranking': row.get('ranking') if row.get('ranking') else None,
                        'logo': row.get('logo', '') or '',
                        'banner': row.get('banner', '') or '',
                    }
                )
        self.stdout.write(self.style.SUCCESS('Universities imported successfully!'))

        # Import Subjects
        with open('data/subjects.csv', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                Subject.objects.update_or_create(
                    subject_code=row['subject_code'],
                    defaults={
                        'name': row['subject_name'],
                        'slug': row['slug']
                    }
                )
        self.stdout.write(self.style.SUCCESS('Subjects imported successfully!'))

        # Import Courses
        with open('data/courses.csv', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                university = University.objects.get(university_id=row['university_id'])
                subject = Subject.objects.filter(subject_code=row['subject_code']).first()
                Course.objects.update_or_create(
                    course_id=row['course_id'],
                    defaults={
                        'university': university,
                        'subject': subject,
                        'title': row['title'],
                        'level': row['level'],
                        'overview': row['overview'],
                        'modules': row['modules'],
                        'entry_requirements': row['entry_requirements'],
                        'duration': row['duration_years'],
                        'location': row['location'],
                        'start_year': row['start_year'],
                        'start_month': row.get('start_month', ''),
                        'course_url': row['course_url'],
                        'intake_months': row.get('start_month', ''),
                    }
                )
        self.stdout.write(self.style.SUCCESS('Courses imported successfully!'))

        # Import Tuition
        with open('data/tuition_fees.csv', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                course = Course.objects.get(course_id=row['course_id'])
                Tuition.objects.update_or_create(
                    course=course,
                    residency=row['residency'],
                    defaults={
                        'tuition_fee': row['tuition_fee'],
                        'scholarship': row['scholarship_amount'],
                        'tuition_deposit': row['tuition_deposit'],
                        'currency': row['currency'],
                    }
                )
        self.stdout.write(self.style.SUCCESS('Tuition imported successfully!'))

        # Import Scholarships
        with open('data/scholarships.csv', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                university = University.objects.get(university_id=row['university_id'])
                # Create unique identifier combining scholarship_id and level to handle duplicates
                unique_id = f"{row['scholarship_id']}_{row['level']}"
                Scholarship.objects.update_or_create(
                    university=university,
                    name=row['name'],
                    level=row['level'],
                    defaults={
                        'eligibility': row['eligibility'],
                        'award_type': row['award_type'],
                        'award_value': row['award_value'],
                        'currency': row['currency'],
                        'deadline': row['deadline'] if row['deadline'] else None,
                        'requirements': row['requirements'],
                        'description': row['description'],
                        'url': row['url'],
                    }
                )
        self.stdout.write(self.style.SUCCESS('Scholarships imported successfully!'))
        
        # Import Rankings for Ireland
        try:
            with open('data/rankings.csv', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        university = University.objects.get(university_id=row['university_id'])
                        year = int(row['academic_year'])
                        
                        UniversityRanking.objects.update_or_create(
                            university=university,
                            source='QS',
                            year=year,
                            defaults={
                                'overall': float(row['overall']) if row['overall'] else None,
                                'world_ranking': int(row['world_ranking']) if row['world_ranking'] and row['world_ranking'] != '0' else None,
                                'arts_humanities': int(row['arts_humanities']) if row['arts_humanities'] and row['arts_humanities'] != '0' else None,
                                'life_sciences': int(row['life_sciences_medicine']) if row['life_sciences_medicine'] and row['life_sciences_medicine'] != '0' else None,
                                'engineering_technology': int(row['engineering_technology']) if row['engineering_technology'] and row['engineering_technology'] != '0' else None,
                                'natural_sciences': int(row['natural_science']) if row['natural_science'] and row['natural_science'] != '0' else None,
                                'social_sciences_management': int(row['social_sciences_management']) if row['social_sciences_management'] and row['social_sciences_management'] != '0' else None,
                                'staff_student_ratio': float(row['staff_student_ratio']) if row['staff_student_ratio'] else None,
                                'international_students_ratio': float(row['international_students_ratio']) if row['international_students_ratio'] else None,
                                'graduate_prospects': float(row['graduate_prospects']) if row['graduate_prospects'] else None,
                                'student_satisfaction': float(row['student_satisfaction']) if row['student_satisfaction'] else None,
                            }
                        )
                    except University.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f"University {row['university_id']} not found, skipping ranking"))
            self.stdout.write(self.style.SUCCESS('Ireland rankings imported successfully!'))
        except FileNotFoundError:
            self.stdout.write(self.style.WARNING('rankings.csv not found, skipping Ireland rankings'))
        
        # Import Rankings for UK
        try:
            with open('data/rankings_uk.csv', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        university = University.objects.get(university_id=row['university_id'])
                        year = int(row['academic_year'])
                        
                        UniversityRanking.objects.update_or_create(
                            university=university,
                            source='QS',
                            year=year,
                            defaults={
                                'overall': float(row['overall']) if row['overall'] else None,
                                'world_ranking': int(row['world_ranking']) if row['world_ranking'] and row['world_ranking'] != '0' else None,
                                'arts_humanities': int(row['arts_humanities']) if row['arts_humanities'] and row['arts_humanities'] != '0' else None,
                                'life_sciences': int(row['life_sciences_medicine']) if row['life_sciences_medicine'] and row['life_sciences_medicine'] != '0' else None,
                                'engineering_technology': int(row['engineering_technology']) if row['engineering_technology'] and row['engineering_technology'] != '0' else None,
                                'natural_sciences': int(row['natural_science']) if row['natural_science'] and row['natural_science'] != '0' else None,
                                'social_sciences_management': int(row['social_sciences_management']) if row['social_sciences_management'] and row['social_sciences_management'] != '0' else None,
                                'staff_student_ratio': float(row['staff_student_ratio']) if row['staff_student_ratio'] else None,
                                'international_students_ratio': float(row['international_students_ratio']) if row['international_students_ratio'] else None,
                                'graduate_prospects': float(row['graduate_prospects']) if row['graduate_prospects'] else None,
                                'student_satisfaction': float(row['student_satisfaction']) if row['student_satisfaction'] else None,
                            }
                        )
                    except University.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f"University {row['university_id']} not found, skipping ranking"))
            self.stdout.write(self.style.SUCCESS('UK rankings imported successfully!'))
        except FileNotFoundError:
            self.stdout.write(self.style.WARNING('rankings_uk.csv not found, skipping UK rankings'))

