// Course Search Results Page
document.addEventListener('DOMContentLoaded', function() {
    // Get URL parameters
    const urlParams = new URLSearchParams(window.location.search);
    let subject = urlParams.get('subject') || '';
    let level = urlParams.get('level') || 'UG';
    let country = urlParams.get('country') || 'UK';
    
    // State
    let allUniversities = [];
    let displayedCount = 0;
    const itemsPerPage = 20;
    
    // Initialize
    updateSearchInfo();
    initializeFilters();
    loadUniversities();
    
    // Update search info text
    function updateSearchInfo() {
        const searchInfo = document.getElementById('search-info');
        if (searchInfo && subject) {
            searchInfo.textContent = `Showing universities offering courses in "${subject}"`;
        }
    }
    
    // Initialize filter buttons and inputs
    function initializeFilters() {
        // Set initial filter values from URL
        const filterSubject = document.getElementById('filter-subject');
        const filterSubjectMobile = document.getElementById('filter-subject-mobile');
        
        if (filterSubject) filterSubject.value = subject;
        if (filterSubjectMobile) filterSubjectMobile.value = subject;
        
        // Level buttons
        document.querySelectorAll('.level-btn, .level-btn-mobile').forEach(btn => {
            const btnLevel = btn.dataset.level;
            if (btnLevel === level) {
                btn.classList.add('active');
                btn.style.backgroundColor = '#102C46';
                btn.style.borderColor = '#102C46';
                btn.style.color = 'white';
            } else {
                btn.classList.remove('active');
                btn.style.backgroundColor = '';
                btn.style.borderColor = '';
                btn.style.color = '#374151'; // gray-700 for inactive state
            }
            
            btn.addEventListener('click', function() {
                level = btnLevel;
                document.querySelectorAll('.level-btn, .level-btn-mobile').forEach(b => {
                    b.classList.remove('active');
                    b.style.backgroundColor = '';
                    b.style.borderColor = '';
                    b.style.color = '#374151'; // gray-700 for inactive state
                });
                this.classList.add('active');
                this.style.backgroundColor = '#102C46';
                this.style.borderColor = '#102C46';
                this.style.color = 'white';
                loadUniversities();
            });
        });
        
        // Country buttons
        document.querySelectorAll('.country-btn, .country-btn-mobile').forEach(btn => {
            const btnCountry = btn.dataset.country;
            if (btnCountry === country) {
                btn.classList.add('active');
                btn.style.backgroundColor = '#102C46';
                btn.style.borderColor = '#102C46';
                btn.style.color = 'white';
            } else {
                btn.classList.remove('active');
                btn.style.backgroundColor = 'white';
                btn.style.borderColor = '#d1d5db';
                btn.style.color = '#111827';
            }
            
            btn.addEventListener('click', function() {
                country = btnCountry;
                document.querySelectorAll('.country-btn, .country-btn-mobile').forEach(b => {
                    b.classList.remove('active');
                    b.style.backgroundColor = 'white';
                    b.style.borderColor = '#d1d5db';
                    b.style.color = '#111827';
                });
                this.classList.add('active');
                this.style.backgroundColor = '#102C46';
                this.style.borderColor = '#102C46';
                this.style.color = 'white';
                loadUniversities();
            });
        });
        
        // Subject search input
        if (filterSubject) {
            filterSubject.addEventListener('input', debounce(function() {
                subject = this.value.trim();
                updateSearchInfo();
                loadUniversities();
            }, 500));
        }
        
        if (filterSubjectMobile) {
            filterSubjectMobile.addEventListener('input', debounce(function() {
                subject = this.value.trim();
                if (filterSubject) filterSubject.value = subject;
                updateSearchInfo();
                loadUniversities();
            }, 500));
        }
        
        // Clear filters
        document.getElementById('clear-filters')?.addEventListener('click', clearFilters);
        document.getElementById('clear-filters-mobile')?.addEventListener('click', clearFilters);
        
        // New Search buttons
        document.getElementById('new-search')?.addEventListener('click', performNewSearch);
        document.getElementById('new-search-mobile')?.addEventListener('click', performNewSearch);
    }
    
    function clearFilters() {
        subject = '';
        level = 'UG';
        country = 'UK';
        
        document.getElementById('filter-subject').value = '';
        document.getElementById('filter-subject-mobile').value = '';
        
        // Reset buttons
        document.querySelectorAll('.level-btn, .level-btn-mobile').forEach(btn => {
            btn.classList.remove('active');
            btn.style.backgroundColor = '';
            btn.style.borderColor = '';
            btn.style.color = '';
            if (btn.dataset.level === 'UG') {
                btn.classList.add('active');
                btn.style.backgroundColor = '#102C46';
                btn.style.borderColor = '#102C46';
                btn.style.color = 'white';
            }
        });
        
        document.querySelectorAll('.country-btn, .country-btn-mobile').forEach(btn => {
            btn.classList.remove('active');
            btn.style.backgroundColor = '';
            btn.style.borderColor = '';
            btn.style.color = '';
            if (btn.dataset.country === 'IE') {
                btn.classList.add('active');
                btn.style.backgroundColor = '#102C46';
                btn.style.borderColor = '#102C46';
                btn.style.color = 'white';
            }
        });
        
        loadUniversities();
    }
    
    // Load universities from API
    async function loadUniversities() {
        try {
            const params = new URLSearchParams({
                subject: subject,
                level: level,
                country: country
            });
            
            const response = await fetch(`/api/course-search/?${params}`);
            const data = await response.json();
            
            allUniversities = data.universities || [];
            displayedCount = 0;
            
            // Update count
            const count = allUniversities.length;
            document.getElementById('university-count').textContent = 
                `${count} ${count === 1 ? 'University' : 'Universities'}`;
            
            // Clear and display results
            document.getElementById('universities-grid').innerHTML = '';
            displayMoreUniversities();
            
        } catch (error) {
            console.error('Error loading universities:', error);
            document.getElementById('university-count').textContent = 'Error loading results';
        }
    }
    
    // Display more universities (pagination)
    function displayMoreUniversities() {
        const grid = document.getElementById('universities-grid');
        const endIndex = Math.min(displayedCount + itemsPerPage, allUniversities.length);
        
        for (let i = displayedCount; i < endIndex; i++) {
            const uni = allUniversities[i];
            grid.appendChild(createUniversityCard(uni));
        }
        
        displayedCount = endIndex;
        
        // Show/hide load more button
        const loadMoreContainer = document.getElementById('load-more-container');
        if (displayedCount < allUniversities.length) {
            loadMoreContainer.classList.remove('hidden');
        } else {
            loadMoreContainer.classList.add('hidden');
        }
    }
    
    // Create university card
    function createUniversityCard(uni) {
        const card = document.createElement('div');
        card.className = 'bg-white dark:bg-secondary rounded-lg shadow-round-box overflow-hidden border border-gray-200 dark:border-gray-700 hover:shadow-xl transition-all';
        
        card.innerHTML = `
            <div class="flex flex-col md:flex-row">
                <div class="md:w-1/3 h-48 md:h-auto">
                    <img src="${uni.banner}" alt="${uni.name}" class="w-full h-full object-cover" onerror="this.src='/static/images/mine/about-us.jpg'">
                </div>
                <div class="md:w-2/3 p-6">
                    <div class="flex justify-between items-start mb-1">
                        <div>
                            <a href="/universities/${uni.slug}/" class="text-xl font-bold text-gray-900 dark:text-white hover:text-primary transition-colors">${uni.name}</a>
                            <a href="/universities/${uni.slug}/" class="block text-gray-600 dark:text-gray-400 hover:text-primary transition-colors mt-1">${uni.city}, ${uni.country === 'IE' ? 'Ireland' : 'UK'}</a>
                        </div>
                        <button class="favorite-btn p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition-all" 
                                data-university-id="${uni.university_id}"
                                data-university-name="${uni.name}"
                                data-university-slug="${uni.slug}"
                                data-university-city="${uni.city}"
                                data-university-country="${uni.country}"
                                data-university-banner="${uni.banner}"
                                title="Add to favorites">
                            <svg class="w-6 h-6 text-gray-400 hover:text-red-500 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z"></path>
                            </svg>
                        </button>
                    </div>
                    ${uni.course_count ? `<p class="text-sm text-gray-500 dark:text-gray-500 mb-4">${uni.course_count} matching ${uni.course_count === 1 ? 'course' : 'courses'} found</p>` : ''}
                    <div class="flex flex-wrap gap-3 mt-4">
                        <a href="/universities/${uni.slug}/" class="px-6 py-2.5 text-white rounded-lg text-sm font-medium transition-all hover:opacity-90" style="background-color: #102C46;">
                            View University
                        </a>
                        <a href="/universities/${uni.slug}/courses/?level=${level}" class="px-6 py-2.5 text-white rounded-lg text-sm font-medium transition-all" style="background-color: #3B82F6;" onmouseover="this.style.backgroundColor='#2563eb'" onmouseout="this.style.backgroundColor='#3B82F6'">
                            View Courses
                        </a>
                    </div>
                </div>
            </div>
        `;
        
        return card;
    }
    
    // Load more button
    document.getElementById('load-more-btn')?.addEventListener('click', async function() {
        const btn = this;
        const spinner = document.getElementById('load-more-spinner');
        const text = document.getElementById('load-more-text');
        
        btn.disabled = true;
        text.textContent = 'Loading...';
        spinner.classList.remove('hidden');
        
        await new Promise(resolve => setTimeout(resolve, 300));
        
        displayMoreUniversities();
        
        btn.disabled = false;
        text.textContent = 'Load More';
        spinner.classList.add('hidden');
    });
    
    // Debounce helper
    function debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }
    
    // Initialize favorites when universities are loaded
    function initializeFavorites() {
        const favorites = JSON.parse(localStorage.getItem('universityFavorites') || '[]');
        
        document.querySelectorAll('.favorite-btn').forEach(btn => {
            const universityId = btn.dataset.universityId;
            const isFavorite = favorites.some(fav => fav.university_id === universityId);
            
            if (isFavorite) {
                const svg = btn.querySelector('svg');
                svg.setAttribute('fill', 'currentColor');
                svg.classList.remove('text-gray-400');
                svg.classList.add('text-red-500');
                btn.title = 'Remove from favorites';
            }
            
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                toggleFavorite(this);
            });
        });
    }
    
    function toggleFavorite(button) {
        const universityData = {
            university_id: button.dataset.universityId,
            name: button.dataset.universityName,
            slug: button.dataset.universitySlug,
            city: button.dataset.universityCity,
            country: button.dataset.universityCountry,
            banner: button.dataset.universityBanner,
            type: 'university'
        };
        
        let favorites = JSON.parse(localStorage.getItem('universityFavorites') || '[]');
        const index = favorites.findIndex(fav => fav.university_id === universityData.university_id);
        
        const svg = button.querySelector('svg');
        
        if (index > -1) {
            favorites.splice(index, 1);
            svg.setAttribute('fill', 'none');
            svg.classList.remove('text-red-500');
            svg.classList.add('text-gray-400');
            button.title = 'Add to favorites';
            showToast(`${universityData.name} removed from favorites`);
        } else {
            favorites.push(universityData);
            svg.setAttribute('fill', 'currentColor');
            svg.classList.remove('text-gray-400');
            svg.classList.add('text-red-500');
            button.title = 'Remove from favorites';
            showToast(`${universityData.name} added to favorites`);
        }
        
        localStorage.setItem('universityFavorites', JSON.stringify(favorites));
    }
    
    function showToast(message) {
        const toast = document.createElement('div');
        toast.className = 'fixed bottom-4 right-4 bg-green-500 text-white px-6 py-3 rounded-lg shadow-lg z-50 animate-fade-in';
        toast.textContent = message;
        document.body.appendChild(toast);
        
        setTimeout(() => {
            toast.remove();
        }, 3000);
    }
    
    // Override the displayMoreUniversities function to initialize favorites after rendering
    const originalDisplayMore = displayMoreUniversities;
    displayMoreUniversities = function() {
        originalDisplayMore();
        initializeFavorites();
    };
    
    // Perform new search with current filters
    function performNewSearch() {
        // Get current filter values - check which button was clicked
        const clickedBtn = event?.target?.closest('button');
        const isMobile = clickedBtn?.id === 'new-search-mobile';
        
        if (isMobile) {
            subject = document.getElementById('filter-subject-mobile')?.value || '';
        } else {
            subject = document.getElementById('filter-subject')?.value || '';
        }
        
        // Update search info text
        updateSearchInfo();
        
        // Show spinner
        const spinner = isMobile ? document.getElementById('new-search-spinner-mobile') : document.getElementById('new-search-spinner');
        const text = isMobile ? document.getElementById('new-search-text-mobile') : document.getElementById('new-search-text');
        const btn = clickedBtn;
        
        if (spinner && text && btn) {
            spinner.classList.remove('hidden');
            text.textContent = 'Searching...';
            btn.disabled = true;
        }
        
        // Load universities with current filters
        loadUniversities();
        
        // Hide spinner after load completes (add delay to ensure UI updates)
        setTimeout(() => {
            if (spinner && text && btn) {
                spinner.classList.add('hidden');
                text.textContent = 'New Search';
                btn.disabled = false;
            }
        }, 1000);
    }
});
