from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from .models import University, Course, Scholarship, Subject, BlogPost
from django.db.models import Q, Count
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.core.paginator import Paginator
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

def compare_tuition(request):
    """API endpoint to compare tuition fees across universities"""
    from .models import Tuition
    
    # Get filter parameters (residence_country is for currency conversion only, not filtering)
    residence_country = request.GET.get('residence', '').strip()
    dest_country = request.GET.get('destination', '').strip().upper()
    programme_type = request.GET.get('programme', '').strip().upper()
    subject_search = request.GET.get('subject', '').strip()
    
    # Validate required parameters (residence not required for filtering)
    if not all([dest_country, programme_type, subject_search]):
        return JsonResponse({'error': 'Missing required parameters'}, status=400)
    
    # Map destination to country code
    country_map = {'UK': 'UK', 'IRELAND': 'IE', 'IE': 'IE'}
    country = country_map.get(dest_country, dest_country)
    
    # Map programme type to level (need to handle both PG and PGT for postgraduate)
    level_map = {
        'UNDERGRADUATE': 'UG', 
        'POSTGRADUATE': 'PG', 
        'UG': 'UG', 
        'PG': 'PG',
        'TOPUP': 'TOPUP',
        'TOP-UP': 'TOPUP',
        'MRES': 'MRES'
    }
    level = level_map.get(programme_type, programme_type)
    
    # Determine residency status (International is most common for comparison)
    residency = 'INTL'
    
    # Query courses matching the criteria using course title instead of subject code
    # For postgraduate, include PG, PGT, and MRES levels
    # For undergraduate, include UG and TOPUP levels
    if level == 'PG':
        courses = Course.objects.filter(
            university__country=country,
            level__in=['PG', 'PGT', 'MRES']
        ).select_related('university', 'subject').prefetch_related('tuitions')
    elif level == 'UG':
        courses = Course.objects.filter(
            university__country=country,
            level__in=['UG', 'TOPUP']
        ).select_related('university', 'subject').prefetch_related('tuitions')
    else:
        # For TOPUP, MRES - match the exact level
        courses = Course.objects.filter(
            university__country=country,
            level=level
        ).select_related('university', 'subject').prefetch_related('tuitions')
    
    # Filter courses by title match (case-insensitive)
    subject_lower = subject_search.lower()
    matching_courses = [course for course in courses if subject_lower in course.title.lower()]
    
    # Prepare comparison data
    results = []
    for course in matching_courses:
        # Get tuition for international students
        tuition = course.tuitions.filter(residency=residency).first()
        
        if tuition:
            # Get available scholarships for this university
            scholarships = course.university.scholarships.filter(level=level).values_list('award_value', flat=True)
            scholarship_info = ', '.join([str(s) for s in scholarships[:3]]) if scholarships else 'Contact University'
            
            results.append({
                'university_id': course.university.university_id,
                'university_name': course.university.name,
                'university_slug': course.university.slug,
                'course_title': course.title,
                'course_id': course.course_id,
                'course_level': course.level,  # Add course level for filtering
                'tuition_fee': float(tuition.tuition_fee),
                'scholarship': float(tuition.scholarship) if tuition.scholarship else 0,
                'net_tuition': float(tuition.net_tuition_fee),
                'tuition_deposit': float(tuition.tuition_deposit) if tuition.tuition_deposit else 0,
                'currency': tuition.currency,
                'duration': course.duration,
                'location': course.location,
                'start_date': f"{course.start_month} {course.start_year}",
                'scholarship_info': scholarship_info,
                'course_url': course.course_url,
            })
    
    # Sort by net tuition (cheapest first)
    results.sort(key=lambda x: x['net_tuition'])
    
    return JsonResponse({
        'results': results,
        'count': len(results),
        'filters': {
            'country': country,
            'level': level,
            'subject': subject_search,
            'residency': residency
        }
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
                'net_fee': tuition.net_tuition_fee
            }
        
        # If EU data doesn't exist, use INTL data for EU
        if 'EU' not in tuition_data and 'INTL' in tuition_data:
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
                recipient_list=[settings.CONTACT_EMAIL],
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

