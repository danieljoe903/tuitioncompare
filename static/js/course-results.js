// Course Results Page JavaScript
document.addEventListener('DOMContentLoaded', function() {
    // Initialize AOS
    AOS.init({
        duration: 1000,
        once: true
    });
    
    // Get URL parameters
    const urlParams = new URLSearchParams(window.location.search);
    const level = urlParams.get('level') || 'undergraduate';
    const country = urlParams.get('country') || '';
    const year = urlParams.get('year') || '';
    const subject = urlParams.get('subject') || '';
    
    // Pre-fill filters with URL parameters
    if (level) {
        const filterLevel = document.getElementById('filter-level');
        if (filterLevel) filterLevel.value = level;
    }
    
    if (country) {
        const filterCountry = document.getElementById('filter-country');
        if (filterCountry) filterCountry.value = country;
    }
    
    if (year) {
        const filterYear = document.getElementById('filter-year');
        if (filterYear) filterYear.value = year;
    }
    
    if (subject) {
        const filterCourse = document.getElementById('filter-course');
        if (filterCourse) filterCourse.value = subject;
    }
    
    // Update page title and breadcrumb
    updatePageHeader(level, subject);
    
    // TODO: Replace with API call to fetch real courses
    const sampleCourses = [];
    
    // Render courses (will show "no results" message until API is implemented)
    renderCourses(sampleCourses);
    
    // Filter functionality
    document.getElementById('apply-filters').addEventListener('click', applyFilters);
    document.getElementById('clear-filters').addEventListener('click', clearFilters);
    
    // Sort functionality
    document.getElementById('sort-results').addEventListener('change', function() {
        sortCourses(this.value);
    });
    
    function generateCourses(subject, level, country, count) {
        // TODO: Replace with API call to fetch real courses from database
        // Example: fetch(`/api/courses/?subject=${subject}&level=${level}&country=${country}`)
        const universities = [];
        
        const coursePrefix = level === 'postgraduate' ? ['MSc', 'MA', 'MRes', 'MBA'] : ['BSc', 'BA', 'BEng', 'LLB'];
        const durations = level === 'postgraduate' ? ['1 Year', '2 Years'] : ['3 Years', '4 Years'];
        const intakes = ['September 2026', 'January 2026', 'October 2026'];
        const tuitionRanges = level === 'postgraduate' ? 
            ['£18,000', '£22,000', '£25,000', '£28,000', '£32,000'] :
            ['£15,000', '£18,500', '£20,000', '£22,500', '£25,000'];
        const scholarships = ['Up to £3,000', 'Up to £5,000', 'Up to £7,500', 'Up to £10,000', 'Not Available'];
        
        // TODO: Return empty array until API is implemented
        return [];
    }
    
    // Filter functionality
    document.getElementById('apply-filters').addEventListener('click', applyFilters);
    document.getElementById('clear-filters').addEventListener('click', clearFilters);
    
    // Sort functionality
    document.getElementById('sort-results').addEventListener('change', function() {
        sortCourses(this.value);
    });
    
    function updatePageHeader(level, subject) {
        const levelText = level === 'undergraduate' ? 'Undergraduate' : 'Postgraduate';
        const breadcrumb = document.getElementById('breadcrumb-level');
        const pageTitle = document.getElementById('page-title');
        const searchSummary = document.getElementById('search-summary');
        
        if (breadcrumb) breadcrumb.textContent = `${levelText} Courses`;
        // Format: "Marketing Undergraduate Courses" or just "Undergraduate Courses" if no subject
        if (pageTitle) pageTitle.textContent = subject ? `${subject} ${levelText} Courses` : `${levelText} Courses`;
        if (searchSummary) searchSummary.textContent = `Showing ${levelText.toLowerCase()} courses${subject ? ' in ' + subject : ''}`;
    }
    
    function renderCourses(courses) {
        const grid = document.getElementById('results-grid');
        const countElement = document.getElementById('results-count');
        
        if (countElement) countElement.textContent = courses.length;
        
        if (!grid) return;
        
        grid.innerHTML = courses.map(course => `
            <div class="bg-white/10 backdrop-blur-md border border-gray-200/30 rounded-lg shadow-lg overflow-hidden hover:shadow-xl transition-shadow" data-aos="fade-up">
                <div class="p-6">
                    <div class="flex justify-between items-start mb-4">
                        <div class="flex-1">
                            <h3 class="text-xl font-bold mb-2 text-MidnightNavyText dark:text-white hover:text-primary transition-colors">
                                <a href="/university-detail">${course.name}</a>
                            </h3>
                            <p class="text-primary font-semibold mb-2">${course.university}</p>
                            <p class="text-sm text-SlateBlueText dark:text-opacity-80 flex items-center">
                                <svg class="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"></path>
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"></path>
                                </svg>
                                ${course.location}
                            </p>
                        </div>
                        <button class="favorite-btn text-gray-400 hover:text-red-500 transition-colors" 
                                onclick="toggleFavorite('course-${course.id}', '${course.name}', '${course.location}', '${course.university}')" 
                                data-university-id="course-${course.id}"
                                title="Add to favorites">
                            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z"></path>
                            </svg>
                        </button>
                    </div>
                    
                    <div class="mb-4">
                        <p class="text-sm text-SlateBlueText dark:text-opacity-80 line-clamp-2">
                            ${course.overview}
                        </p>
                    </div>
                    
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4 py-4 border-y border-gray-200 dark:border-gray-700">
                        <div>
                            <p class="text-xs text-SlateBlueText dark:text-opacity-80 mb-1">Duration</p>
                            <p class="font-semibold text-sm">${course.duration}</p>
                        </div>
                        <div>
                            <p class="text-xs text-SlateBlueText dark:text-opacity-80 mb-1">Intake</p>
                            <p class="font-semibold text-sm">${course.intake}</p>
                        </div>
                        <div>
                            <p class="text-xs text-SlateBlueText dark:text-opacity-80 mb-1">Tuition Fee</p>
                            <p class="font-semibold text-sm text-primary">${course.tuition}/year</p>
                        </div>
                        <div>
                            <p class="text-xs text-SlateBlueText dark:text-opacity-80 mb-1">Scholarship</p>
                            <p class="font-semibold text-sm text-green-600">${course.scholarship}</p>
                        </div>
                    </div>
                    
                    <div class="mb-4">
                        <h4 class="font-bold text-sm mb-2">Entry Requirements:</h4>
                        <p class="text-sm text-SlateBlueText dark:text-opacity-80">${course.requirements}</p>
                    </div>
                    
                    <div class="flex flex-wrap gap-3">
                        <a href="/university-detail" class="btn btn-1 hover-filled-slide-down rounded-lg overflow-hidden flex-1 sm:flex-none">
                            <span class="!py-2 !px-6">View Details</span>
                        </a>
                        <a href="#" target="_blank" class="btn_outline btn-2 hover-outline-slide-down rounded-lg flex-1 sm:flex-none">
                            <span class="!py-2 !px-6">Visit Website</span>
                        </a>
                    </div>
                </div>
            </div>
        `).join('');
        
        // Add favorite button functionality
        document.querySelectorAll('.favorite-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                this.classList.toggle('text-red-500');
                const svg = this.querySelector('svg');
                if (this.classList.contains('text-red-500')) {
                    svg.setAttribute('fill', 'currentColor');
                } else {
                    svg.setAttribute('fill', 'none');
                }
            });
        });
    }
    
    function applyFilters() {
        const filterLevel = document.getElementById('filter-level').value;
        const filterCountry = document.getElementById('filter-country').value;
        const filterYear = document.getElementById('filter-year').value;
        const filterCourse = document.getElementById('filter-course').value.trim();
        
        // Filter logic would go here
        console.log('Filters applied:', { filterLevel, filterCountry, filterYear, filterCourse });
        
        // Regenerate courses based on current parameters
        const sampleCourses = generateCourses(subject, level, country, 10);
        renderCourses(sampleCourses);
    }
    
    function clearFilters() {
        document.getElementById('filter-level').value = '';
        document.getElementById('filter-country').value = '';
        document.getElementById('filter-year').value = '';
        document.getElementById('filter-course').value = '';
        
        const sampleCourses = generateCourses(subject, level, country, 10);
        renderCourses(sampleCourses);
    }
    
    function sortCourses(sortBy) {
        const sampleCourses = generateCourses(subject, level, country, 10);
        let sorted = [...sampleCourses];
        
        switch(sortBy) {
            case 'tuition-low':
                sorted.sort((a, b) => parseInt(a.tuition.replace(/[^0-9]/g, '')) - parseInt(b.tuition.replace(/[^0-9]/g, '')));
                break;
            case 'tuition-high':
                sorted.sort((a, b) => parseInt(b.tuition.replace(/[^0-9]/g, '')) - parseInt(a.tuition.replace(/[^0-9]/g, '')));
                break;
            case 'duration':
                sorted.sort((a, b) => parseInt(a.duration) - parseInt(b.duration));
                break;
            case 'university':
                sorted.sort((a, b) => a.university.localeCompare(b.university));
                break;
        }
        
        renderCourses(sorted);
    }
});
