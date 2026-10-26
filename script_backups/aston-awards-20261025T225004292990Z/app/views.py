from .scholarship_calculation import calculate_scholarships
from .fee_models import fee_metadata

from django.shortcuts import render, get_object_or_404, redirect
from app.services import (
    calculate_aru_deposit,get_aru_eligibility_questions,
    boolean_answer
)
from django.http import JsonResponse
from .models import Tuition, University, Course, Scholarship, Subject, BlogPost
from app.deposit_calculation import calculate_aberystwyth_deposit
from django.db.models import Q, Count

from django.contrib import messages

from django.core.mail import send_mail

from django.conf import settings
from app.deposit_calculation import calculate_abertay_deposit

from django.core.paginator import Paginator
"""Read-only comparison endpoint. Abertay categories are not reused for other schools."""
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from django.http import JsonResponse
from django.db.models.functions import Lower
from .models import Course, TuitionSource
from .fee_models import fee_metadata
from .fee_selection import FEE_CATEGORIES, select_published_fee
from .scholarship_calculation import calculate_scholarships
import random

from difflib import SequenceMatcher



def home(request):

    """Render the home page with recommended universities"""

    # Get 4 random Irish universities with banner images for the recommended section

    irish_universities = list(University.objects.filter(country='IE').exclude(banner='').exclude(banner__isnull=True))

    recommended_universities = random.sample(irish_universities, min(4, len(irish_universities))) if irish_universities else []

    return render(request, 'index.html', {'recommended_universities': recommended_universities})



def university_search(request):

    """Render the university search page"""

    return render(request, 'university-search.html')



def get_universities(request):

    """API endpoint to fetch universities with filters"""

    universities = University.objects.all()



    # Apply filters

    university_id = request.GET.get('university_id', '').strip()

    slug = request.GET.get('slug', '').strip()

    name = request.GET.get('name', '').strip()

    country = request.GET.get('country', '').strip()

    level = request.GET.get('level', '').strip()



    if university_id:

        universities = universities.filter(university_id=university_id)



    if slug:

        universities = universities.filter(slug=slug)



    if name:

        universities = universities.filter(name__icontains=name)



    if country:

        universities = universities.filter(country=country)



    if level:

        # Filter universities that have courses of the specified level

        # Only apply this filter if universities have any courses

        universities_with_courses = universities.filter(courses__level=level).distinct()

        # If the filter returns results, use it; otherwise keep all universities (for countries without courses yet)

        if universities_with_courses.exists():

            universities = universities_with_courses



    # Prepare data

    data = []

    for uni in universities:

        # Check if this is a detail page request (slug filter) or search page

        is_detail_page = slug and slug == uni.slug

        overview_text = uni.overview if is_detail_page else (uni.overview[:150] + '...' if len(uni.overview) > 150 else uni.overview)



        # Count UG courses for this university

        ug_courses_count = uni.courses.filter(level='UG').count()

        # Count PG courses (include both PG and PGT)

        pg_courses_count = uni.courses.filter(level__in=['PG', 'PGT']).count()



        # Get world ranking from UniversityRanking model

        world_ranking = None

        try:

            from .models import UniversityRanking

            ranking_obj = UniversityRanking.objects.filter(university=uni).first()

            if ranking_obj:

                world_ranking = ranking_obj.world_ranking

        except:

            pass



        data.append({

            'university_id': uni.university_id,

            'name': uni.name,

            'slug': uni.slug,

            'country': uni.country,

            'city': uni.city,

            'overview': overview_text,

            'website': uni.website,

            'logo': uni.logo if uni.logo else None,

            'banner': uni.banner if uni.banner else None,

            'avg_ug_tuition': float(uni.avg_ug_tuition) if uni.avg_ug_tuition else None,

            'avg_pg_tuition': float(uni.avg_pg_tuition) if uni.avg_pg_tuition else None,

            'ranking': uni.ranking,

            'world_ranking': world_ranking,

            'population': uni.population,

            'acceptance_rate': float(uni.acceptance_rate) if uni.acceptance_rate else None,

            'estimated_living_cost_annual': float(uni.estimated_living_cost) if uni.estimated_living_cost else None,

            'admission_requirements': uni.admission_requirements if is_detail_page else None,

            'graduation_rate': float(uni.graduation_rate) if uni.graduation_rate else None,

            'ug_courses_count': ug_courses_count,

            'pg_courses_count': pg_courses_count,

        })



    return JsonResponse({'universities': data, 'count': len(data)})



def get_scholarships(request):

    """API endpoint to fetch scholarships for a university"""

    university_id = request.GET.get('university_id', '').strip()



    if not university_id:

        return JsonResponse({'scholarships': [], 'count': 0})



    scholarships = Scholarship.objects.filter(university__university_id=university_id)



    data = []

    for scholarship in scholarships:

        data.append({

            'name': scholarship.name,

            'level': scholarship.level,  # Return actual value (UG/PG) not display name

            'level_display': scholarship.get_level_display(),  # Keep display name for UI

            'eligibility': scholarship.eligibility,

            'award_type': scholarship.award_type,

            'award_value': scholarship.award_value,

            'currency': scholarship.currency,

            'deadline': scholarship.deadline.strftime('%B %d, %Y') if scholarship.deadline else 'Rolling basis',

            'requirements': scholarship.requirements,

            'description': scholarship.description,

            'url': scholarship.url,

            'university_id': scholarship.university.university_id if scholarship.university else None,

            'university_name': scholarship.university.name if scholarship.university else 'Various Universities',

            'university_country': scholarship.university.country if scholarship.university else None,

        })



    return JsonResponse({'scholarships': data, 'count': len(data)})



def university_courses(request, slug):

    """Render the university courses page"""

    return render(request, 'university-courses.html')



def get_courses(request):

    """API endpoint to fetch courses with filters"""

    courses = Course.objects.all()



    # Apply filters

    university_id = request.GET.get('university_id', '').strip()

    level = request.GET.get('level', '').strip()

    subject = request.GET.get('subject', '').strip()



    if university_id:

        courses = courses.filter(university__university_id=university_id)



    if level:

        # For postgraduate, include both PG and PGT levels

        if level == 'PG':

            courses = courses.filter(level__in=['PG', 'PGT'])

        else:

            courses = courses.filter(level=level)



    if subject:

        courses = courses.filter(subject__icontains=subject)



    # Order by course title

    courses = courses.order_by('title')



    # Prepare data

    data = []

    for course in courses:

        data.append({

            'course_id': course.course_id,

            'course_title': course.title,

            'level': course.level,

            'course_url': course.course_url,

            'subject': course.subject.name if course.subject else None,

            'location': course.location,

            'university_id': course.university.university_id,

            'university_name': course.university.name,

            'university_website': course.university.website,

        })



    return JsonResponse(data, safe=False)



def course_search_page(request):

    """Render the course search results page"""

    return render(request, 'course-search.html')



def subjects_api(request):

    """API endpoint for subject autocomplete"""

    query = request.GET.get('q', '').strip()

    subjects = Subject.objects.all()



    if query:

        subjects = subjects.filter(name__icontains=query)



    subjects = subjects.order_by('name')[:20]  # Limit to 20 suggestions



    data = [{

        'subject_code': subject.subject_code,

        'name': subject.name,

        'slug': subject.slug

    } for subject in subjects]



    return JsonResponse({'subjects': data})



def search_courses_api(request):

    """API endpoint for course search with fuzzy matching"""

    subject = request.GET.get('subject', '').strip()

    level = request.GET.get('level', '').strip()

    country = request.GET.get('country', '').strip()



    if not subject:

        return JsonResponse({'universities': [], 'count': 0})



    # Find courses with fuzzy matching

    # First try exact match, then partial match, then fuzzy match

    courses = Course.objects.all()



    if level:

        courses = courses.filter(level=level)



    # Apply fuzzy matching on course titles

    matching_courses = []

    subject_lower = subject.lower()



    for course in courses:

        title_lower = course.title.lower()

        # Calculate similarity ratio

        ratio = SequenceMatcher(None, subject_lower, title_lower).ratio()



        # Also check if subject is contained in title

        if subject_lower in title_lower:

            ratio = max(ratio, 0.7)  # Boost partial matches



        # Check if any word in subject matches any word in title

        subject_words = subject_lower.split()

        title_words = title_lower.split()

        word_matches = sum(1 for sw in subject_words for tw in title_words if sw in tw or tw in sw)

        if word_matches > 0:

            ratio = max(ratio, 0.5 + (word_matches * 0.1))



        # Include courses with similarity > 0.4 (40% match)

        if ratio >= 0.4:

            matching_courses.append((course, ratio))



    # Sort by similarity (highest first)

    matching_courses.sort(key=lambda x: x[1], reverse=True)



    # Get unique universities from matching courses

    university_ids = set()

    university_course_count = {}



    for course, ratio in matching_courses:

        uni_id = course.university.university_id

        # Apply country filter

        if country and course.university.country != country:

            continue



        university_ids.add(uni_id)

        university_course_count[uni_id] = university_course_count.get(uni_id, 0) + 1



    # Fetch university details

    universities = University.objects.filter(university_id__in=university_ids)



    # Prepare data

    data = []

    for uni in universities:

        data.append({

            'university_id': uni.university_id,

            'name': uni.name,

            'slug': uni.slug,

            'country': uni.country,

            'city': uni.city,

            'banner': uni.banner if uni.banner else '/static/images/mine/about-us.jpg',

            'logo': uni.logo if uni.logo else None,

            'website': uni.website,

            'course_count': university_course_count.get(uni.university_id, 0),

        })



    # Sort by course count (universities with more matching courses first)

    data.sort(key=lambda x: x['course_count'], reverse=True)



    return JsonResponse({'universities': data, 'count': len(data)})



def compare_rankings(request):

    """Render the compare rankings page"""

    return render(request, 'compare-rankings.html')



def get_rankings(request):

    """API endpoint to fetch university rankings by country"""

    from .models import UniversityRanking



    country = request.GET.get('country', '').strip().upper()

    if country not in ['UK', 'IE']:

        return JsonResponse({'error': 'Invalid country. Use UK or IE.'}, status=400)



    # Get latest rankings for the country

    rankings = UniversityRanking.objects.filter(

        university__country=country

    ).select_related('university').order_by('-overall')



    data = []

    for ranking in rankings:

        uni = ranking.university

        data.append({

            'university_id': uni.university_id,

            'university_name': uni.name,

            'university_slug': uni.slug,

            'city': uni.city,

            'overall': float(ranking.overall) if ranking.overall else 0,

            'arts_humanities': ranking.arts_humanities if ranking.arts_humanities else 0,

            'life_sciences_medicine': ranking.life_sciences if ranking.life_sciences else 0,

            'engineering_technology': ranking.engineering_technology if ranking.engineering_technology else 0,

            'natural_science': ranking.natural_sciences if ranking.natural_sciences else 0,

            'social_sciences_management': ranking.social_sciences_management if ranking.social_sciences_management else 0,

            'staff_student_ratio': float(ranking.staff_student_ratio) if ranking.staff_student_ratio else 0,

            'international_students_ratio': float(ranking.international_students_ratio) if ranking.international_students_ratio else 0,

            'graduate_prospects': float(ranking.graduate_prospects) if ranking.graduate_prospects else 0,

            'student_satisfaction': float(ranking.student_satisfaction) if ranking.student_satisfaction else 0,

        })



    return JsonResponse({'rankings': data, 'count': len(data)})

def abertay_fee_verification_error(tuition, source):
    """Check previously imported Abertay international fee evidence."""
    if source is None:
        return (
            "This course's international tuition fee "
            "has not been verified."
        )

    supported_sources = {
        "2025/26": (
            "https://www.abertay.ac.uk/study-apply/"
            "money-fees-and-funding/tuition-fees-2025-2026/"
        ),
        "2026/27": (
            "https://www.abertay.ac.uk/study-apply/"
            "money-fees-and-funding/tuition-fees-2026-2027/"
        ),
    }

    expected_url = supported_sources.get(source.academic_year)

    if expected_url is None:
        return "The fee's academic year has not been verified."

    stored_course_url = (
        tuition.course.course_url or ""
    ).strip().rstrip("/")

    source_url = (
        source.source_url or ""
    ).strip().rstrip("/")

    official_course_page = (
        stored_course_url.startswith(
            "https://www.abertay.ac.uk/course-search/"
        )
        and stored_course_url
        != "https://www.abertay.ac.uk/course-search"
    )

    central_source_matches = (
        source_url == expected_url.rstrip("/")
    )

    course_source_matches = (
        official_course_page
        and source_url == stored_course_url
    )

    if not (central_source_matches or course_source_matches):
        return "The fee source requires verification."

    if source.fee_basis != "annual_tuition":
        return "The published fee basis requires verification."

    if (
        tuition.residency != "INTL"
        or tuition.currency != "GBP"
    ):
        return (
            "The international fee status or currency "
            "requires review."
        )

    raw = source.raw_fees

    if not isinstance(raw, dict):
        return "The fee evidence is missing or invalid."

    if raw.get("source_fee_category") != "International":
        return (
            "The published international fee category "
            "is unconfirmed."
        )

    categories = raw.get("fee_categories")

    if not isinstance(categories, list):
        return "Published fee-category evidence is missing."

    matches = [
        category
        for category in categories
        if isinstance(category, dict)
        and category.get("source_fee_category") == "International"
        and category.get("academic_year") == source.academic_year
    ]

    if len(matches) != 1:
        return (
            "A unique published international fee "
            "could not be confirmed."
        )

    category = matches[0]

    if (
        category.get("currency") != "GBP"
        or category.get("amount_requires_review") is not False
        or category.get("footnote_marker")
    ):
        return "The published international fee requires review."

    try:
        published = Decimal(str(category["annual_tuition"]))
        stored = Decimal(str(tuition.tuition_fee))
    except (
        KeyError,
        InvalidOperation,
        TypeError,
        ValueError,
    ):
        return "The published or stored fee amount is invalid."

    if (
        not published.is_finite()
        or not stored.is_finite()
        or published <= 0
        or stored <= 0
        or published != stored
    ):
        return (
            "The stored tuition does not match "
            "its published fee evidence."
        )

    return None


def compare_tuition(request):
    """Compare tuition, applicable scholarships and deposit policies."""

    # Aston first30 comparison v1
    from app.scraping.uk.aston.calculation import (fee_error as aston_fee_error, fee_metadata as aston_metadata, calculate as aston_calculate, deposit as aston_deposit)
    # AUP reviewed23 comparison v1
    from app.scraping.uk.aup.calculation import (fee_error as aup_fee_error, fee_metadata as aup_metadata, saved_profile as aup_profile, calculate as aup_calculate, deposit as aup_deposit)

    # AUB remaining route comparison v2
    # AUB reviewed campus comparison v1
    from app.scraping.uk.aub.remaining_calculation import (fee_error as aub_fee_error, fee_metadata as aub_metadata, saved_profile as aub_profile, calculate as aub_calculate, deposit as aub_deposit, comparison_filter as aub_filter, review_summary as aub_review_summary)

    # Arden level fee integration v3
    # Arden route basis integration v2
    # Arden campus PG integration v1
    from app.scraping.uk.arden.study_calculation import (
        comparison_amount as arden_amount, fee_metadata as arden_fee_metadata,
        verification_error as arden_level_fee_error, filter_matches as arden_filter,
        build_profile as arden_profile, selection_error as arden_selection_error,
        calculate_deposit as arden_deposit, eligibility_questions as arden_questions,
    )

    comparison_basis = request.GET.get("comparison_basis", "annual").strip()
    if comparison_basis not in {"annual", "programme_total", "per_level"}:
        return JsonResponse({"error": "Invalid comparison_basis."}, status=400)

    residence = request.GET.get("residence", "").strip().upper()
    destination = request.GET.get("destination", "").strip().upper()
    programme = request.GET.get("programme", "").strip().upper()
    subject = " ".join(request.GET.get("subject", "").split())

    fee_status = request.GET.get(
        "fee_status", "international"
    ).strip().lower()

    if not all([destination, programme, subject]):
        return JsonResponse(
            {"error": "Missing required parameters."},
            status=400,
        )

    country = {
        "UK": "UK",
        "IRELAND": "IE",
        "IE": "IE",
    }.get(destination)

    level = {
        "UNDERGRADUATE": "UG",
        "POSTGRADUATE": "PG",
        "UG": "UG",
        "PG": "PG",
        "PGT": "PG",
        "TOPUP": "TOPUP",
        "TOP-UP": "TOPUP",
        "MRES": "MRES",
    }.get(programme)

    if country is None or level is None:
        return JsonResponse(
            {"error": "Invalid destination or programme type."},
            status=400,
        )

    if fee_status != "international":
        return JsonResponse(
            {
                "error": (
                    "This comparison currently supports "
                    "international fee status only."
                )
            },
            status=400,
        )

    levels = {
        "UG": ["UG", "TOPUP"],
        "PG": ["PG", "PGT", "MRES"],
        "TOPUP": ["TOPUP"],
        "MRES": ["MRES"],
    }

    def parse_boolean(value):
        value = value.strip().lower()

        if value in {"yes", "true", "1"}:
            return True

        if value in {"no", "false", "0"}:
            return False

        return None

    profile = {"residence": residence}

    boolean_fields = (
        # General scholarship eligibility.
        "new_entrant",
        "full_time",
        "on_campus_dundee",
        "academic_conditions_met",
        "partner_discount",
        "repeat_year",
        "pg_2_1_equivalent_met",
        "offer_received",
        "applied_by_deadline",
        "external_funding",

        # Visa, funding and payment eligibility.
        "requires_student_visa",
        "self_funded",
        "full_fee_paying",
        "sponsored",

        # ARU eligibility.
        "apcl",
        "registration_conditions_met",
        "aru_funding_eligibility_confirmed",
        "aru_ug_award_confirmed",
        "aru_graduate",
        "aru_pg_entry_requirements_exceeded",
        "aru_pg_eligibility_confirmed",
        "aru_course_award_applicability_confirmed",
        "aru_international_route_eligibility_confirmed",
        "aru_london_alumni",
        "uk_first_class_degree",
        "aru_london_award_confirmed",
    )

    for key in boolean_fields:
        raw_answer = request.GET.get(key)

        # An omitted answer remains unknown.
        if raw_answer is None or not raw_answer.strip():
            continue

        answer = parse_boolean(raw_answer)

        if answer is None:
            return JsonResponse(
                {
                    "error": (
                        f"Invalid answer for {key}. "
                        "Use yes or no, or omit an unknown answer."
                    )
                },
                status=400,
            )

        profile[key] = answer

    entry_year = request.GET.get("entry_year", "").strip()

    if entry_year:
        try:
            entry_year_number = int(entry_year)
        except ValueError:
            return JsonResponse(
                {"error": "entry_year must be an integer."},
                status=400,
            )

        if not 1 <= entry_year_number <= 6:
            return JsonResponse(
                {"error": "entry_year must be between 1 and 6."},
                status=400,
            )

        profile["entry_year"] = entry_year_number

    award_tier = request.GET.get(
        "aru_ug_award_tier", ""
    ).strip().lower()

    if award_tier:
        if award_tier not in {"lower", "higher"}:
            return JsonResponse(
                {
                    "error": (
                        "aru_ug_award_tier must be lower or higher. "
                        "Omit it when the tier is unknown."
                    )
                },
                status=400,
            )

        profile["aru_ug_award_tier"] = award_tier

    residence_region = request.GET.get(
        "residence_region", ""
    ).strip()

    def merge_questions(*groups):
        """Preserve saved question definitions without duplicate keys."""
        merged = {}

        for group in groups:
            if not isinstance(group, list):
                continue

            for question in group:
                if (
                    isinstance(question, dict)
                    and question.get("key")
                ):
                    merged.setdefault(question["key"], question)

        return list(merged.values())

    def review_questions(raw, review_name):
        if not isinstance(raw, dict):
            return []

        review = raw.get(review_name)

        if not isinstance(review, dict):
            return []

        questions = review.get("eligibility_questions", [])

        return questions if isinstance(questions, list) else []

    def saved_scholarship_questions(
        awards,
        course_id,
        academic_year,
    ):
        """Return saved questions, filling gaps from rule conditions."""

        labels = {
            "new_entrant": (
                "Are you joining this university as a new student?"
            ),
            "full_time": "Will you study full-time?",
            "offer_received": (
                "Have you received an offer for this course?"
            ),
            "external_funding": (
                "Will you receive external funding for your studies?"
            ),
            "applied_by_deadline": (
                "Did you apply by the scholarship's published deadline?"
            ),
            "academic_conditions_met": (
                "Have you met the scholarship's academic conditions?"
            ),
            "entry_year": "Which course year will you enter?",
            "apcl": (
                "Have you received Accredited Prior Certified Learning?"
            ),
            "registration_conditions_met": (
                "Have all university registration conditions been met?"
            ),
            "aru_ug_award_confirmed": (
                "Has ARU confirmed your undergraduate scholarship?"
            ),
            "aru_ug_award_tier": (
                "Which undergraduate scholarship tier has ARU confirmed?"
            ),
            "aru_graduate": "Are you an ARU graduate?",
            "aru_pg_entry_requirements_exceeded": (
                "Has ARU confirmed that your qualifications exceed "
                "the scholarship's minimum entry requirements?"
            ),
            "aru_pg_eligibility_confirmed": (
                "Has ARU confirmed your postgraduate "
                "scholarship eligibility?"
            ),
            "aru_funding_eligibility_confirmed": (
                "Has ARU confirmed that your funding arrangement "
                "is eligible for this scholarship?"
            ),
        }

        # These facts come from the selected course or fee record.
        automatic_fields = {
            "course_id",
            "university_id",
            "level",
            "fee_status",
            "currency",
            "course_kind",
            "postgraduate_taught",
            "arden_online_gross_quote",
            "arden_comparison_period",
            "arden_online_tier2_country_confirmed",
        }

        questions = {}

        for award in awards:
            try:
                rule = award.calculation_rule
            except AttributeError:
                continue

            data = rule.rules

            if (
                not rule.verified
                or not rule.source_url
                or not isinstance(data, dict)
                or data.get("academic_year") != academic_year
            ):
                continue

            study_years = data.get("study_years")

            # The comparison currently calculates study year 1.
            if (
                not isinstance(study_years, list)
                or 1 not in study_years
            ):
                continue

            conditions = data.get("conditions")

            if not isinstance(conditions, dict):
                continue

            scope = conditions.get("course_id")

            if scope is not None:
                if not isinstance(scope, list):
                    continue

                if str(course_id) not in {
                    str(value) for value in scope
                }:
                    continue

            saved = data.get("eligibility_questions", [])

            if not isinstance(saved, list):
                saved = []

            # Preserve explicit question definitions.
            for question in saved:
                if (
                    isinstance(question, dict)
                    and question.get("key")
                    and question["key"] not in automatic_fields
                ):
                    questions.setdefault(
                        question["key"],
                        dict(question),
                    )

            # Build missing definitions from actual saved conditions.
            for key, allowed in conditions.items():
                if (
                    key in automatic_fields
                    or key in questions
                    or not isinstance(allowed, list)
                    or not allowed
                ):
                    continue

                question = {
                    "key": key,
                    "question": labels.get(
                        key,
                        (
                            "Please provide your answer for "
                            f"{key.replace('_', ' ')}."
                        ),
                    ),
                }

                if all(type(value) is bool for value in allowed):
                    # Both yes and no must remain possible answers.
                    question["type"] = "boolean"

                elif all(type(value) is int for value in allowed):
                    question["type"] = "integer"

                elif all(isinstance(value, str) for value in allowed):
                    question["type"] = "choice"
                    question["choices"] = [
                        {
                            "value": value,
                            "label": value.replace("_", " ").capitalize(),
                        }
                        for value in allowed
                    ]
                    question["note"] = (
                        "These are the values covered by this rule. "
                        "Leave unanswered if unknown; do not select "
                        "an inaccurate answer."
                    )

                else:
                    question["type"] = "text"

                questions[key] = question

        return list(questions.values())
    def reviewed_fee_error(tuition, source):
        """Verify Aberystwyth and ARU fees before any deduction."""
        if str(tuition.course.university_id) == "UK007":
            return aston_fee_error(tuition, source)
        if str(tuition.course.university_id) == "UK006":
            return aup_fee_error(tuition, source)
        if str(tuition.course.university_id) == "UK005":
            return aub_fee_error(tuition, source, comparison_basis=comparison_basis)
        if source is None:
            return (
                "International tuition has not been verified "
                "for this course and intake."
            )

        raw = source.raw_fees

        if not isinstance(raw, dict):
            return "The saved tuition evidence requires review."

        display_notes = raw.get("display_notes")
        if not isinstance(display_notes, dict):
            display_notes = {}

        fee_review = raw.get("fee_review")
        if not isinstance(fee_review, dict):
            fee_review = {}

        reason = (
            raw.get("verification_reason")
            or display_notes.get("fee")
            or fee_review.get("reason")
            or (
                "International tuition verification is pending "
                "for this course and intake."
            )
        )

        if (
            raw.get("verified") is not True
            or not source.academic_year
            or not source.source_url
        ):
            return reason

        if (str(tuition.course.university_id) == "UK004"
                and raw.get("importer") == "arden_online_ug_levels_v3"):
            return arden_level_fee_error(tuition, source)

        if source.fee_basis not in {
            "annual_tuition",
            "first_year_tuition",
            "full_time_course_fee",
        } and not (
            str(tuition.course.university_id) == "UK004"
            and source.fee_basis == "total_programme_fee"
            and raw.get("importer") in {"arden_online_pg_v2", "arden_online_ug_topup_v3"}
        ):
            return (
                "A supported verified tuition amount is unavailable."
            )

        if (
            raw.get("academic_year") != source.academic_year
            or raw.get("currency") != tuition.currency
            or raw.get("source_fee_category") != "International"
        ):
            return (
                "The tuition source period, currency or "
                "fee category requires review."
            )

        try:
            stored = Decimal(str(tuition.tuition_fee))
            published = Decimal(str(
                raw["total_programme_fee"]
                if str(tuition.course.university_id) == "UK004"
                and source.fee_basis == "total_programme_fee"
                else raw["annual_tuition"]
            ))

            if (
                not stored.is_finite()
                or not published.is_finite()
                or stored <= 0
                or published <= 0
                or stored != published
            ):
                return (
                    "Stored tuition does not match its "
                    "verified source amount."
                )

        except (
            KeyError,
            InvalidOperation,
            ValueError,
            TypeError,
        ):
            return "The published tuition amount requires review."

        return None

    courses = (
        Course.objects
        .annotate(comparison_search_title=Lower("title"))
        .filter(
            university__country=country,
            is_active=True,
            level__in=levels[level],
            comparison_search_title__contains=subject.lower(),
        )
        .select_related("university", "subject")
        .prefetch_related(
            "tuitions",
            "tuitions__source_metadata",
            "university__scholarships__calculation_rule",
        )
    )

    results = []

    for course in courses:
        university = course.university
        university_id = str(course.university_id)

        abertay = country == "UK" and university_id == "UK001"
        aberystwyth = country == "UK" and university_id == "UK002"
        aru = country == "UK" and university_id == "UK003"
        arden = country == "UK" and university_id == "UK004"
        aub = country == "UK" and university_id == "UK005"
        aup = country == "UK" and university_id == "UK006"
        aston = country == "UK" and university_id == "UK007"

        eligibility_questions = {
            "scholarship": [],
            "deposit": [],
        }

        base = {
            "university_id": course.university_id,
            "university_name": university.name,
            "university_slug": university.slug,
            "course_title": course.title,
            "course_id": course.course_id,
            "course_level": course.level,
            "duration": course.duration,
            "location": course.location,
            "start_date": (
                f"{course.start_month or ''} "
                f"{course.start_year or ''}"
            ).strip(),
            "course_url": course.course_url,
            "fee_status": "international",
        }

        def unavailable(reason):
            return {
                **base,
                "fee_available": False,
                "fee_verified": False,
                "fee_verification_status": "needs_review",
                "fee_label": "Fee verification required",
                "fee_note": reason,
                "academic_year": "",
                "currency": university.currency,
                "source_fee_category": None,
                "fee_source_url": "",
                "tuition_fee": None,
                "scholarship": None,
                "net_tuition": None,
                "scholarship_calculation": None,
                "tuition_deposit": None,
                "deposit_calculation": None,
                "eligibility_questions": eligibility_questions,
                "scholarship_info": (
                    "No deduction calculated without a confirmed fee."
                ),
            }

        tuitions = [
            item
            for item in course.tuitions.all()
            if item.residency == "INTL"
        ]

        if len(tuitions) != 1:
            results.append(
                unavailable(
                    "A unique international tuition record "
                    "is unavailable."
                )
            )
            continue

        tuition = tuitions[0]

        try:
            source = tuition.source_metadata
        except TuitionSource.DoesNotExist:
            source = None

        raw = (
            source.raw_fees
            if source is not None
            and isinstance(source.raw_fees, dict)
            else {}
        )

        if aub and source is not None:
            base.update(aub_review_summary(source, raw))

        # Programme totals are explicitly requested and never ranked with annual fees.
        if comparison_basis != "annual" and not (
            (arden and arden_filter(raw, comparison_basis)) or (aub and aub_filter(raw, comparison_basis))
        ):
            continue

        # Preserve pending-route questions even when the fee is unavailable.
        eligibility_questions = {
            "scholarship": review_questions(
                raw, "scholarship_review"
            ),
            "deposit": review_questions(raw, "deposit_review"),
        }

        if abertay:
            reason = abertay_fee_verification_error(tuition, source)

            if reason:
                results.append(unavailable(reason))
                continue

        if aberystwyth or aru or arden or aub or aup or aston:
            reason = reviewed_fee_error(tuition, source)

            if reason:
                results.append(unavailable(reason))
                continue

        if arden:
            reason = arden_selection_error(request, raw)
            if reason:
                results.append(unavailable(reason))
                continue

        try:
            amount = arden_amount(request, raw, tuition) if arden else Decimal(str(tuition.tuition_fee))
        except (InvalidOperation, TypeError, ValueError):
            results.append(
                unavailable("The stored tuition amount is invalid.")
            )
            continue

        if not amount.is_finite() or amount <= 0:
            results.append(
                unavailable("The stored tuition amount is invalid.")
            )
            continue

        metadata = {} if (arden or aub or aup or aston) else dict(fee_metadata(tuition) or {})

        academic_year = (
            source.academic_year
            if source is not None and source.academic_year
            else metadata.get("academic_year")
        )

        if not academic_year:
            if course.start_year is None:
                results.append(
                    unavailable("The applicable fee year is missing.")
                )
                continue

            # Fallback for universities not yet covered by reviewed adapters.
            academic_year = (
                f"{course.start_year}/"
                f"{(course.start_year + 1) % 100:02d}"
            )

        currency = tuition.currency
        fee_source_url = source.source_url if source else ""

        if abertay:
            categories = raw.get("fee_categories", [])

            if not isinstance(categories, list):
                categories = []

            category = next(
                (
                    item
                    for item in categories
                    if isinstance(item, dict)
                    and item.get("source_fee_category")
                    == "International"
                    and item.get("academic_year") == academic_year
                ),
                {},
            )

            note = (
                "Published international fee; the university "
                "confirms individual eligibility."
            )

            annotation = category.get("annotation")

            if isinstance(annotation, str) and annotation.strip():
                note += " " + annotation.strip()

            metadata = {
                "fee_label": "Annual tuition fee",
                "academic_year": academic_year,
                "fee_note": note,
            }

        elif aberystwyth or aru:
            metadata = {
                **metadata,
                "fee_label": {
                    "annual_tuition": "Annual tuition fee",
                    "first_year_tuition": "First-year tuition fee",
                    "full_time_course_fee": (
                        "Published full-time course fee"
                    ),
                }.get(source.fee_basis, "Published tuition fee"),
                "academic_year": academic_year,
                "fee_note": (
                    raw.get("fee_note")
                    or (
                        "Published international tuition for the "
                        "reviewed course and fee period. The university "
                        "confirms individual eligibility."
                    )
                ),
            }

        if arden:
            metadata.update(arden_fee_metadata(request, source, raw))
        if aub:
            metadata.update(aub_metadata(source, raw))
        if aup:
            metadata.update(aup_metadata(source, raw))
        if aston:
            metadata.update(aston_metadata(source, raw))

        award_level = {
            "PGT": "PG",
            "TOPUP": "UG",
        }.get(course.level, course.level)

        awards = [
            award
            for award in university.scholarships.all()
            if award.level == award_level
        ]

        comparison_tuition = SimpleNamespace(
            tuition_fee=amount,
            scholarship=tuition.scholarship,
            scholarship_source_id=getattr(
                tuition, "scholarship_source_id", None
            ),
            currency=currency,
            residency="INTL",
            course=course,
        )

        course_profile = dict(profile)
        if aup:
            course_profile.update(aup_profile(request, course.course_id, academic_year))
        if aub:
            course_profile.update(aub_profile(request, course.course_id, academic_year))
        if arden:
            course_profile.update(arden_profile(request, raw))
            eligibility_questions["scholarship"] = merge_questions(
                eligibility_questions["scholarship"], arden_questions(request, raw),
            )

        if abertay:
            course_profile["postgraduate_taught"] = (
                raw.get("course_kind") == "postgraduate_taught"
            )

        # The calculator matches the course, fee year, study year,
        # eligibility conditions and permitted award combinations.
        # Unknown personal facts are not automatically set to True.
        # AUB early-payment source guard v1
        if aub:
            comparison_tuition.source_metadata = source
        if aup:
            comparison_tuition.source_metadata = source
        calculation = (aston_calculate if aston else aup_calculate if aup else aub_calculate if aub else calculate_scholarships)(
            comparison_tuition,
            awards,
            course_profile,
            academic_year=academic_year,
            study_year=1,
            allow_unverified_legacy=(
                university_id not in {"UK001", "UK002", "UK003", "UK004", "UK005", "UK006", "UK007"}
            ),
        )

        if arden:
            calculation["comparison_period"] = metadata["comparison_period"]
            if metadata["comparison_period"] == "per_level":
                calculation.setdefault("notes", []).append(
                    "Programme-wide awards require programme_total; no unverified per-level allocation is deducted."
                )

        discount = Decimal(str(calculation["discount"]))
        net_tuition = Decimal(str(calculation["net"]))

        scholarship_info = "; ".join(
            (
                f"{award['name']}: {currency} "
                f"{'up to ' if award.get('is_maximum') else ''}"
                f"{award['amount']} "
                "(for the compared study year)"
            )
            for award in calculation.get("applied", [])
        )

        if not scholarship_info:
            scholarship_info = (
                "Stored discount applied; eligibility unverified"
                if discount > 0
                else "No verified award applied"
            )

        if aub and discount == 0:
            scholarship_info = "Scholarship eligibility or award amount not confirmed. Complete the scholarship page."

        if arden and metadata["comparison_period"] == "total_programme":
            scholarship_info = scholarship_info.replace(
                "(for the compared study year)", "(for the full programme)"
            )

        deposit_calculation = {
            "available": False,
            "amount": None,
            "currency": currency,
            "academic_year": academic_year,
            "source_url": None,
            "policy_checked_at": None,
            "rule": None,
            "is_estimate": True,
            "label": "Deposit assessment required",
            "note": (
                "Deposit rules have not been verified "
                "for this university."
            ),
            "missing_facts": [],
        }

        if abertay:
            deposit_calculation = calculate_abertay_deposit(
                academic_year=academic_year,
                tuition_fee=amount,
                scholarship_calculation=calculation,
                legal_residence=residence,
                residence_region=residence_region,
                requires_student_visa=profile.get(
                    "requires_student_visa"
                ),
                self_funded=profile.get("self_funded"),
            )

        elif aberystwyth:
            deposit_calculation = calculate_aberystwyth_deposit(
                academic_year=academic_year,
            )

        elif arden:
            deposit_calculation = arden_deposit(
                tuition=tuition, academic_year=academic_year,
            )

        elif aston:
            deposit_calculation = aston_deposit(tuition=tuition, academic_year=academic_year, profile=course_profile)

        elif aup:
            deposit_calculation = aup_deposit(tuition=tuition, academic_year=academic_year, profile=course_profile)

        elif aub:
            deposit_calculation = aub_deposit(tuition=tuition, academic_year=academic_year)

        elif aru:
            deposit_calculation = calculate_aru_deposit(
                tuition=tuition,
                academic_year=academic_year,
                profile=course_profile,
            )

            aru_questions = get_aru_eligibility_questions(
                tuition=tuition,
                academic_year=academic_year,
            ) or {}

            eligibility_questions = {
                "scholarship": merge_questions(
                    eligibility_questions["scholarship"],
                    aru_questions.get("scholarship", []),
                ),
                "deposit": merge_questions(
                    eligibility_questions["deposit"],
                    aru_questions.get("deposit", []),
                ),
            }

        # Return saved scholarship questions for Aberystwyth too.
        eligibility_questions["scholarship"] = merge_questions(
            eligibility_questions["scholarship"],
            saved_scholarship_questions(
                awards,
                course.course_id,
                academic_year,
            ),
        )

        if aub:
            eligibility_questions = {"scholarship": [], "deposit": []}
            if course.course_id in {"C_UK005_23", "C_UK005_40", "C_UK005_41", "C_UK005_42"}:
                # AUB online progression labels v1
                scholarship_info = ("AUB online progression bursary deducted for the confirmed saved eligibility." if discount > 0 else "Online progression bursary available; saved eligibility and award confirmation required.")

        if aup:
            eligibility_questions = {"scholarship": [], "deposit": []}
            scholarship_info = "Uses the confirmed Alumni deduction for this intake." if discount > 0 else "Scholarship eligibility or cohort applicability requires confirmation. Visit the scholarship page."

        if aston:
            eligibility_questions = {"scholarship": [], "deposit": []}
            scholarship_info = "Aston scholarship applicability requires exact-cohort review. Visit the scholarship page."

        verified_fee = abertay or aberystwyth or aru or arden or aub or aup or aston

        results.append({
            **base,
            **metadata,
            "academic_year": academic_year,
            "fee_available": True,
            "fee_verified": verified_fee,
            "fee_verification_status": (
                "verified" if verified_fee else "not_checked"
            ),
            "source_fee_category": "International",
            "fee_source_url": fee_source_url,
            "currency": currency,

            # Gross tuition is retained separately.
            "tuition_fee": float(amount),

            # Applicable scholarship and resulting net tuition.
            "scholarship": float(discount),
            "net_tuition": float(net_tuition),
            **({"arden_selection": {
                "campus": course_profile.get("arden_campus"),
                "intake": course_profile.get("arden_intake"),
            }} if arden else {}),
            "scholarship_calculation": calculation,
            "scholarship_info": scholarship_info,

            "tuition_deposit": (
                float(deposit_calculation["amount"])
                if deposit_calculation.get("available")
                and deposit_calculation.get("amount") is not None
                else None
            ),
            "deposit_calculation": deposit_calculation,
            "eligibility_questions": eligibility_questions,
        })

    results.sort(
        key=lambda result: (
            not result["fee_available"],
            result["currency"] or "",
            (
                result["net_tuition"]
                if result["net_tuition"] is not None
                else 0
            ),
        )
    )

    return JsonResponse({
        "results": results,
        "count": len(results),
        "filters": {
            "country": country,
            "level": level,
            "subject": subject,
            "fee_status": "international",
            "residency": "INTL",
            "comparison_basis": comparison_basis,
        },
    })


def course_detail(request, course_id):

    """Render the course detail page"""

    try:

        course = Course.objects.select_related('university', 'subject').get(course_id=course_id)



        # Get all tuition records for this course

        tuitions = course.tuitions.all()



        # Organize tuition by residency type

        tuition_data = {}

        for tuition in tuitions:

            tuition_data[tuition.residency] = {

                'fee': tuition.tuition_fee,

                'scholarship': tuition.scholarship,

                'deposit': tuition.tuition_deposit,

                'currency': tuition.currency,

                **fee_metadata(tuition),

                'net_fee': tuition.net_tuition_fee

            }



        # If EU data doesn't exist, use INTL data for EU

        if 'EU' not in tuition_data and 'INTL' in tuition_data and not tuition_data['INTL'].get('academic_year'):

            tuition_data['EU'] = tuition_data['INTL'].copy()



        # Get scholarships for this university at the course level

        scholarships = Scholarship.objects.filter(

            university=course.university,

            level=course.level

        )



        context = {

            'course': course,

            'tuition_data': tuition_data,

            'scholarships': scholarships,

        }



        return render(request, 'course-detail.html', context)



    except Course.DoesNotExist:

        return render(request, '404.html', status=404)





def contact_us(request):

    """Handle contact form submissions"""

    if request.method == 'POST':

        # Get form data

        first_name = request.POST.get('first_name', '').strip()

        last_name = request.POST.get('last_name', '').strip()

        email = request.POST.get('email', '').strip()

        phone = request.POST.get('phone', '').strip()

        subject = request.POST.get('subject', '').strip()

        message = request.POST.get('message', '').strip()



        # Basic validation

        if not all([first_name, last_name, email, subject, message]):

            messages.error(request, 'Please fill in all required fields.')

            return render(request, 'contact-us.html')



        # Prepare email content

        email_subject = f'Contact Form: {subject}'

        email_message = f"""

New contact form submission:



Name: {first_name} {last_name}

Email: {email}

Phone: {phone if phone else 'Not provided'}

Subject: {subject}



Message:

{message}



---

This email was sent from the University Tuition Compare contact form.

        """



        try:

            # Send email to info@universitytuitioncompare.com

            send_mail(

                subject=email_subject,

                message=email_message,

                from_email=settings.DEFAULT_FROM_EMAIL,

                recipient_list=[settings.EMAIL_HOST_USER],

                fail_silently=False,

            )



            messages.success(request, f'Thank you {first_name}! Your enquiry has been submitted successfully. We will get back to you soon.')

        except Exception as e:

            # Log error and show user-friendly message

            print(f"Error sending email: {e}")

            messages.error(request, 'There was an error submitting your enquiry. Please try again or email us directly at info@universitytuitioncompare.com')



        return render(request, 'contact-us.html')



    # GET request - just render the form

    return render(request, 'contact-us.html')



def blog_list(request):

    """Render the blog list page"""

    # Get all published blog posts

    posts = BlogPost.objects.filter(status='published').order_by('-published_at')



    # Pagination - 9 posts per page

    paginator = Paginator(posts, 9)

    page_number = request.GET.get('page', 1)

    page_obj = paginator.get_page(page_number)



    context = {

        'page_obj': page_obj,

    }

    return render(request, 'blog-list.html', context)



def blog_detail(request, slug):

    """Render the blog detail page"""

    from .models import Comment

    post = get_object_or_404(BlogPost, slug=slug, status='published')



    # Handle comment submission

    if request.method == 'POST':

        name = request.POST.get('name')

        email = request.POST.get('email')

        comment_text = request.POST.get('comment')



        if name and email and comment_text:

            Comment.objects.create(

                post=post,

                name=name,

                email=email,

                comment=comment_text,

                is_approved=False

            )

            from django.contrib import messages

            messages.success(request, 'Your comment has been submitted and is awaiting approval.')

            return redirect('blog-detail', slug=slug)



    # Increment view count

    post.views += 1

    post.save(update_fields=['views'])



    # Get approved comments

    approved_comments = post.comments.filter(is_approved=True).order_by('-created_at')



    context = {

        'post': post,

        'approved_comments': approved_comments,

    }

    return render(request, 'blog-detail.html', context)





def get_exchange_rates(request):

    """API endpoint to fetch active exchange rates"""

    from .models import ExchangeRate



    rates = ExchangeRate.objects.filter(is_active=True)



    data = {

        'rates': {},

        'symbols': {}

    }



    for rate in rates:

        data['rates'][rate.currency_code] = float(rate.rate_to_gbp)

        data['symbols'][rate.currency_code] = rate.currency_symbol



    return JsonResponse(data)


