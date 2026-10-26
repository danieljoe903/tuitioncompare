import csv
import os
from django.core.management.base import BaseCommand
from app.models import University, Subject, Course, Tuition

class Command(BaseCommand):
    help = "Import Top-up and MRes programme data from CSV files (courses_tp.csv, tuition_tp.csv, courses_mres.csv, tuition_mres.csv)"

    def handle(self, *args, **kwargs):
        # Import Top-up Courses
        courses_file = 'data/courses_tp.csv'
        if os.path.exists(courses_file):
            with open(courses_file, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                courses_imported = 0
                courses_skipped = 0
                
                for row in reader:
                    try:
                        university = University.objects.get(university_id=row['university_id'])
                        subject = Subject.objects.filter(subject_code=row['subject_code']).first()
                        
                        # Ensure level is set to TOPUP (the CSV should have "topup" or "TOPUP" in level column)
                        level_value = row['level'].upper()
                        if level_value not in ['TOPUP', 'TOP-UP']:
                            level_value = 'TOPUP'
                        
                        Course.objects.update_or_create(
                            course_id=row['course_id'],
                            defaults={
                                'university': university,
                                'subject': subject,
                                'title': row['title'],
                                'level': 'TOPUP',  # Force TOPUP level
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
                        courses_imported += 1
                    except University.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f"University {row['university_id']} not found, skipping course {row['course_id']}"))
                        courses_skipped += 1
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Error importing course {row['course_id']}: {str(e)}"))
                        courses_skipped += 1
                
                self.stdout.write(self.style.SUCCESS(f'Top-up courses imported: {courses_imported}, skipped: {courses_skipped}'))
        else:
            self.stdout.write(self.style.WARNING(f'{courses_file} not found. Please place the file in the data directory.'))

        # Import Top-up Tuition
        tuition_file = 'data/tuition_tp.csv'
        if os.path.exists(tuition_file):
            with open(tuition_file, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                tuition_imported = 0
                tuition_skipped = 0
                
                for row in reader:
                    try:
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
                        tuition_imported += 1
                    except Course.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f"Course {row['course_id']} not found, skipping tuition"))
                        tuition_skipped += 1
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Error importing tuition for {row['course_id']}: {str(e)}"))
                        tuition_skipped += 1
                
                self.stdout.write(self.style.SUCCESS(f'Top-up tuition fees imported: {tuition_imported}, skipped: {tuition_skipped}'))
        else:
            self.stdout.write(self.style.WARNING(f'{tuition_file} not found. Please place the file in the data directory.'))
        
        # Import MRes Courses
        courses_mres_file = 'data/courses_mres.csv'
        if os.path.exists(courses_mres_file):
            with open(courses_mres_file, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                courses_imported = 0
                courses_skipped = 0
                
                for row in reader:
                    try:
                        university = University.objects.get(university_id=row['university_id'])
                        subject = Subject.objects.filter(subject_code=row['subject_code']).first()
                        
                        Course.objects.update_or_create(
                            course_id=row['course_id'],
                            defaults={
                                'university': university,
                                'subject': subject,
                                'title': row['title'],
                                'level': 'MRES',  # Force MRES level
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
                        courses_imported += 1
                    except University.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f"University {row['university_id']} not found, skipping course {row['course_id']}"))
                        courses_skipped += 1
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Error importing course {row['course_id']}: {str(e)}"))
                        courses_skipped += 1
                
                self.stdout.write(self.style.SUCCESS(f'MRes courses imported: {courses_imported}, skipped: {courses_skipped}'))
        else:
            self.stdout.write(self.style.WARNING(f'{courses_mres_file} not found. Please place the file in the data directory.'))

        # Import MRes Tuition
        tuition_mres_file = 'data/tuition_mres.csv'
        if os.path.exists(tuition_mres_file):
            with open(tuition_mres_file, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                tuition_imported = 0
                tuition_skipped = 0
                
                for row in reader:
                    try:
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
                        tuition_imported += 1
                    except Course.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f"Course {row['course_id']} not found, skipping tuition"))
                        tuition_skipped += 1
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Error importing tuition for {row['course_id']}: {str(e)}"))
                        tuition_skipped += 1
                
                self.stdout.write(self.style.SUCCESS(f'MRes tuition fees imported: {tuition_imported}, skipped: {tuition_skipped}'))
        else:
            self.stdout.write(self.style.WARNING(f'{tuition_mres_file} not found. Please place the file in the data directory.'))
        
        self.stdout.write(self.style.SUCCESS('Import process completed!'))
