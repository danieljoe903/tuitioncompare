// Global variables
let currentUniversity = null;
let currentLevel = 'UG';
let currentPage = 1;
const coursesPerPage = 20;
let allCourses = [];
let displayedCourses = [];
let searchQuery = '';

// Get URL parameters
function getUrlParams() {
    const params = new URLSearchParams(window.location.search);
    const pathParts = window.location.pathname.split('/').filter(p => p);
    // URL format: /universities/{slug}/courses/
    // pathParts will be ['universities', 'slug', 'courses']
    const slug = pathParts[1]; // Get the slug which is at index 1
    return {
        slug: slug,
        level: params.get('level') || 'UG'
    };
}

// Initialize page
document.addEventListener('DOMContentLoaded', function() {
    const params = getUrlParams();
    currentLevel = params.level;
    
    // Set active level button
    setActiveLevelButton(currentLevel);
    
    // Load university and courses
    loadUniversity(params.slug);
    
    // Setup event listeners
    setupEventListeners();
});

// Setup event listeners
function setupEventListeners() {
    // Desktop level buttons
    document.querySelectorAll('.level-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const level = this.dataset.level;
            if (level !== currentLevel) {
                currentLevel = level;
                setActiveLevelButton(level);
                resetPagination();
                loadCourses();
            }
        });
    });
    
    // Mobile level buttons
    document.querySelectorAll('.level-btn-mobile').forEach(btn => {
        btn.addEventListener('click', function() {
            const level = this.dataset.level;
            if (level !== currentLevel) {
                currentLevel = level;
                setActiveLevelButton(level);
                resetPagination();
                loadCourses();
            }
        });
    });
    
    // Load more button
    document.getElementById('load-more-btn').addEventListener('click', function() {
        const loadMoreText = document.getElementById('load-more-text');
        const loadMoreSpinner = document.getElementById('load-more-spinner');
        
        // Show loading state
        this.disabled = true;
        if (loadMoreText) loadMoreText.textContent = 'Loading...';
        if (loadMoreSpinner) loadMoreSpinner.classList.remove('hidden');
        
        // Simulate loading delay
        setTimeout(() => {
            currentPage++;
            displayMoreCourses();
            
            // Reset button state
            this.disabled = false;
            if (loadMoreText) loadMoreText.textContent = 'Load More Courses';
            if (loadMoreSpinner) loadMoreSpinner.classList.add('hidden');
        }, 300);
    });
    
    // Desktop search
    const searchInput = document.getElementById('course-search');
    if (searchInput) {
        searchInput.addEventListener('input', function(e) {
            searchQuery = e.target.value.toLowerCase();
            if (allCourses.length > 0) {
                resetPagination();
                displayCourses();
            }
            // Sync with mobile
            const mobileSearch = document.getElementById('course-search-mobile');
            if (mobileSearch) mobileSearch.value = e.target.value;
        });
    }
    
    // Mobile search
    const searchInputMobile = document.getElementById('course-search-mobile');
    if (searchInputMobile) {
        searchInputMobile.addEventListener('input', function(e) {
            searchQuery = e.target.value.toLowerCase();
            if (allCourses.length > 0) {
                resetPagination();
                displayCourses();
            }
            // Sync with desktop
            const desktopSearch = document.getElementById('course-search');
            if (desktopSearch) desktopSearch.value = e.target.value;
        });
    }
}

// Set active level button
function setActiveLevelButton(level) {
    // Desktop buttons
    document.querySelectorAll('.level-btn').forEach(btn => {
        if (btn.dataset.level === level) {
            btn.classList.add('active');
            btn.style.backgroundColor = '#102C46';
            btn.style.borderColor = '#102C46';
            btn.classList.remove('border-gray-300', 'dark:border-gray-600', 'bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300');
            btn.classList.add('text-white');
        } else {
            btn.classList.remove('active', 'text-white');
            btn.style.backgroundColor = '';
            btn.style.borderColor = '';
            btn.classList.add('border-gray-300', 'dark:border-gray-600', 'bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300');
        }
    });
    
    // Mobile buttons
    document.querySelectorAll('.level-btn-mobile').forEach(btn => {
        if (btn.dataset.level === level) {
            btn.classList.add('active');
            btn.style.backgroundColor = '#102C46';
            btn.style.borderColor = '#102C46';
            btn.classList.remove('border-gray-300', 'dark:border-gray-600', 'bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300');
            btn.classList.add('text-white');
        } else {
            btn.classList.remove('active', 'text-white');
            btn.style.backgroundColor = '';
            btn.style.borderColor = '';
            btn.classList.add('border-gray-300', 'dark:border-gray-600', 'bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300');
        }
    });
}

// Load university data
async function loadUniversity(slug) {
    try {
        console.log('Loading university with slug:', slug);
        const response = await fetch(`/api/universities/?slug=${slug}`);
        const data = await response.json();
        
        if (data.universities && data.universities.length > 0) {
            currentUniversity = data.universities[0];
            displayUniversityInfo();
            loadCourses();
        } else {
            document.getElementById('university-course-card').innerHTML = `
                <div class="text-center py-12">
                    <p class="text-gray-600 dark:text-gray-400">University not found</p>
                </div>
            `;
        }
    } catch (error) {
        console.error('Error loading university:', error);
        document.getElementById('university-course-card').innerHTML = `
            <div class="text-center py-12">
                <p class="text-red-600 dark:text-red-400">Error loading university data</p>
            </div>
        `;
    }
}

// Display university info
function displayUniversityInfo() {
    if (!currentUniversity) return;
    
    // Update page title
    document.getElementById('page-title').textContent = `${currentUniversity.name} Courses`;
    document.getElementById('page-subtitle').textContent = `Browse ${currentLevel === 'UG' ? 'undergraduate' : 'postgraduate'} courses at ${currentUniversity.name}`;
    
    // Update back buttons
    const backUrl = `/universities/${currentUniversity.slug}/`;
    document.getElementById('back-to-university').href = backUrl;
    document.getElementById('back-to-university-mobile').href = backUrl;
}

// Load courses
async function loadCourses() {
    if (!currentUniversity) return;
    
    try {
        const response = await fetch(`/api/courses/?university_id=${currentUniversity.university_id}&level=${currentLevel}`);
        allCourses = await response.json();
        
        resetPagination();
        displayCourses();
    } catch (error) {
        console.error('Error loading courses:', error);
        document.getElementById('university-course-card').innerHTML = `
            <div class="text-center py-12">
                <p class="text-red-600 dark:text-red-400">Error loading courses</p>
            </div>
        `;
    }
}

// Reset pagination
function resetPagination() {
    currentPage = 1;
    displayedCourses = [];
}

// Display courses
function displayCourses() {
    // Filter courses by search query
    let filteredCourses = allCourses;
    if (searchQuery) {
        filteredCourses = allCourses.filter(course => 
            course.course_title.toLowerCase().includes(searchQuery) ||
            (course.subject && course.subject.toLowerCase().includes(searchQuery))
        );
    }
    
    displayedCourses = filteredCourses.slice(0, coursesPerPage * currentPage);
    
    // Update course count
    document.getElementById('course-count').textContent = filteredCourses.length;
    
    if (displayedCourses.length === 0) {
        document.getElementById('university-course-card').innerHTML = `
            <div class="text-center py-12">
                <p class="text-gray-600 dark:text-gray-400">No ${currentLevel === 'UG' ? 'undergraduate' : 'postgraduate'} courses found for this university</p>
            </div>
        `;
        document.getElementById('load-more-btn').classList.add('hidden');
        return;
    }
    
    renderUniversityCard();
    
    // Show/hide load more button
    if (displayedCourses.length < filteredCourses.length) {
        document.getElementById('load-more-btn').classList.remove('hidden');
    } else {
        document.getElementById('load-more-btn').classList.add('hidden');
    }
}

// Display more courses
function displayMoreCourses() {
    displayCourses();
}

// Render university card with courses
function renderUniversityCard() {
    const card = `
        <div class="bg-white/10 backdrop-blur-md border border-gray-200/30 rounded-lg shadow-lg overflow-hidden">
            <!-- University Header -->
            <div class="p-6 border-b border-gray-200/20">
                <div class="flex items-start justify-between">
                    <div class="flex items-start gap-4">
                        <img src="${currentUniversity.logo || '/static/images/mine/default-uni-logo.png'}" 
                             alt="${currentUniversity.name}" 
                             class="w-16 h-16 object-contain rounded-lg border border-gray-200/30 bg-white/5 p-2"
                             onerror="this.src='/static/images/mine/default-uni-logo.png'">
                        <div>
                            <h3 class="text-xl font-semibold text-gray-900 dark:text-white mb-1">
                                ${currentUniversity.name}
                            </h3>
                            <p class="text-gray-600 dark:text-gray-400 flex items-center">
                                <svg class="w-4 h-4 mr-1" fill="currentColor" viewBox="0 0 20 20">
                                    <path fill-rule="evenodd" d="M5.05 4.05a7 7 0 119.9 9.9L10 18.9l-4.95-4.95a7 7 0 010-9.9zM10 11a2 2 0 100-4 2 2 0 000 4z" clip-rule="evenodd"></path>
                                </svg>
                                ${currentUniversity.city}, ${currentUniversity.country}
                            </p>
                        </div>
                    </div>
                    <button class="favorite-btn p-2 hover:bg-gray-100 dark:hover:bg-dark_input rounded-full transition-colors" data-university-id="${currentUniversity.university_id}">
                        <svg class="w-6 h-6 text-gray-400 hover:text-red-500 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z"></path>
                        </svg>
                    </button>
                </div>
            </div>
            
            <!-- Courses List -->
            <div class="divide-y divide-gray-200 dark:divide-gray-700">
                ${renderCoursesList()}
            </div>
        </div>
    `;
    
    document.getElementById('university-course-card').innerHTML = card;
    
    // Setup favorite button for university
    setupUniversityFavorite();
    
    // Setup details buttons for courses
    setupCourseDetails();
}

// Render courses list
function renderCoursesList() {
    return displayedCourses.map(course => {
        const universityWebsite = course.university_website && !course.university_website.startsWith('http') 
            ? `https://${course.university_website}` 
            : course.university_website;
        
        return `
        <div class="p-6 hover:bg-white/5 transition-colors border-b border-gray-200/20 last:border-b-0">
            <!-- Desktop Layout -->
            <div class="hidden lg:flex items-center justify-between gap-4">
                <a href="/courses/${course.course_id}/" class="flex-1 cursor-pointer hover:opacity-80 transition-opacity">
                    <h4 class="text-base font-medium text-gray-900 dark:text-white mb-1">
                        ${course.course_title}
                    </h4>
                    ${course.location ? `<p class="text-sm text-gray-600 dark:text-gray-400">${course.location}</p>` : ''}
                </a>
                <div class="flex items-center gap-3">
                    <button class="course-details-btn px-6 py-2.5 text-sm font-medium text-white rounded-lg hover:opacity-90 transition-all whitespace-nowrap" style="background-color: #3B82F6;" data-course-id="${course.course_id}">
                        Details
                    </button>
                    ${universityWebsite ? `
                        <a href="${universityWebsite}" target="_blank" class="px-6 py-2.5 text-sm font-medium text-white rounded-lg hover:opacity-90 transition-all whitespace-nowrap" style="background-color: #102C46;">
                            Website
                        </a>
                    ` : ''}
                </div>
            </div>
            
            <!-- Mobile Layout -->
            <div class="lg:hidden">
                <a href="/courses/${course.course_id}/" class="flex-1 mb-3 block cursor-pointer hover:opacity-80 transition-opacity">
                    <h4 class="text-base font-medium text-gray-900 dark:text-white mb-1">
                        ${course.course_title}
                    </h4>
                    ${course.location ? `<p class="text-sm text-gray-600 dark:text-gray-400">${course.location}</p>` : ''}
                </a>
                <div class="grid grid-cols-2 gap-3">
                    <button class="course-details-btn px-6 py-2.5 text-sm font-medium text-white rounded-lg hover:opacity-90 transition-all" style="background-color: #3B82F6;" data-course-id="${course.course_id}">
                        Details
                    </button>
                    ${universityWebsite ? `
                        <a href="${universityWebsite}" target="_blank" class="px-6 py-2.5 text-sm font-medium text-white rounded-lg hover:opacity-90 transition-all text-center" style="background-color: #102C46;">
                            Website
                        </a>
                    ` : '<div></div>'}
                </div>
            </div>
        </div>
    `;
    }).join('');
}

// Setup university favorite button
function setupUniversityFavorite() {
    const favoriteBtn = document.querySelector('.favorite-btn');
    if (!favoriteBtn || !currentUniversity) return;
    
    const universityData = {
        university_id: currentUniversity.university_id,
        name: currentUniversity.name,
        slug: currentUniversity.slug,
        city: currentUniversity.city,
        country: currentUniversity.country,
        banner: currentUniversity.banner,
        type: 'university'
    };
    
    // Check if already favorited
    const favorites = JSON.parse(localStorage.getItem('universityFavorites') || '[]');
    const isFavorite = favorites.some(fav => fav.university_id === universityData.university_id);
    
    if (isFavorite) {
        favoriteBtn.querySelector('svg').classList.add('text-red-500');
        favoriteBtn.querySelector('svg').setAttribute('fill', 'currentColor');
        favoriteBtn.title = 'Remove from favorites';
    }
    
    favoriteBtn.addEventListener('click', function() {
        const svg = this.querySelector('svg');
        let favorites = JSON.parse(localStorage.getItem('universityFavorites') || '[]');
        const index = favorites.findIndex(fav => fav.university_id === universityData.university_id);
        
        if (index > -1) {
            // Remove from favorites
            favorites.splice(index, 1);
            svg.classList.remove('text-red-500');
            svg.setAttribute('fill', 'none');
            this.title = 'Add to favorites';
            showToast(`${universityData.name} removed from favorites`);
        } else {
            // Add to favorites
            favorites.push(universityData);
            svg.classList.add('text-red-500');
            svg.setAttribute('fill', 'currentColor');
            this.title = 'Remove from favorites';
            showToast(`${universityData.name} added to favorites`);
        }
        
        localStorage.setItem('universityFavorites', JSON.stringify(favorites));
    });
}

// Setup course favorite buttons
function showToast(message) {
    const toast = document.createElement('div');
    toast.className = 'fixed bottom-4 right-4 bg-green-500 text-white px-6 py-3 rounded-lg shadow-lg z-50';
    toast.textContent = message;
    document.body.appendChild(toast);
    
    setTimeout(() => {
        toast.remove();
    }, 3000);
}

// Setup course details buttons
function setupCourseDetails() {
    const detailsBtns = document.querySelectorAll('.course-details-btn');
    
    detailsBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            const courseId = this.dataset.courseId;
            // Navigate to course details page
            window.location.href = `/courses/${courseId}/`;
        });
    });
}
