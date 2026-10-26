// University Search and Filter functionality

let allUniversities = [];
let filteredUniversities = [];
let displayedUniversities = [];
let currentPage = 0;
const UNIVERSITIES_PER_PAGE = 10;
let activeLevel = 'UG'; // Default to Undergraduate
let activeCountry = 'UK'; // Default to UK

// Load universities on page load
document.addEventListener('DOMContentLoaded', function() {
    initializeFilterButtons();
    setupEventListeners();
    loadUniversities();
});

function initializeFilterButtons() {
    // Initialize level buttons
    document.querySelectorAll('.level-btn, .level-btn-mobile').forEach(btn => {
        if (btn.classList.contains('active')) {
            btn.style.backgroundColor = '#102C46';
            btn.style.borderColor = '#102C46';
        }
    });
    
    // Initialize country buttons
    document.querySelectorAll('.country-btn, .country-btn-mobile').forEach(btn => {
        if (btn.classList.contains('active')) {
            btn.style.backgroundColor = '#102C46';
            btn.style.borderColor = '#102C46';
        }
    });
}

function setupEventListeners() {
    // Search input - Desktop
    const searchInput = document.getElementById('filter-name');
    if (searchInput) {
        searchInput.addEventListener('input', debounce(function() {
            const searchInputMobile = document.getElementById('filter-name-mobile');
            if (searchInputMobile) searchInputMobile.value = this.value || '';
            applyFilters();
        }, 300));
    }
    
    // Search input - Mobile
    const searchInputMobile = document.getElementById('filter-name-mobile');
    if (searchInputMobile) {
        searchInputMobile.addEventListener('input', debounce(function() {
            const searchInput = document.getElementById('filter-name');
            if (searchInput) searchInput.value = this.value || '';
            applyFilters();
        }, 300));
    }
    
    // Level buttons - Desktop
    document.querySelectorAll('.level-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            handleLevelButtonClick(this, '.level-btn');
        });
    });
    
    // Level buttons - Mobile
    document.querySelectorAll('.level-btn-mobile').forEach(btn => {
        btn.addEventListener('click', function() {
            handleLevelButtonClick(this, '.level-btn-mobile');
        });
    });
    
    // Country buttons - Desktop
    document.querySelectorAll('.country-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            handleCountryButtonClick(this, '.country-btn');
        });
    });
    
    // Country buttons - Mobile
    document.querySelectorAll('.country-btn-mobile').forEach(btn => {
        btn.addEventListener('click', function() {
            handleCountryButtonClick(this, '.country-btn-mobile');
        });
    });
    
    // Clear filters button - Desktop
    const clearBtn = document.getElementById('clear-filters');
    if (clearBtn) {
        clearBtn.addEventListener('click', clearFilters);
    }
    
    // Clear filters button - Mobile
    const clearBtnMobile = document.getElementById('clear-filters-mobile');
    if (clearBtnMobile) {
        clearBtnMobile.addEventListener('click', clearFilters);
    }
    
    // Load more button
    const loadMoreBtn = document.getElementById('load-more-btn');
    if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', loadMoreUniversities);
    }
}

function handleLevelButtonClick(clickedBtn, selector) {
    // Remove active class from all level buttons in this group
    document.querySelectorAll(selector).forEach(b => {
        b.classList.remove('active', 'text-white');
        b.classList.add('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
        b.style.backgroundColor = '';
        b.style.borderColor = '';
    });
    
    // Add active class to clicked button
    clickedBtn.classList.add('active', 'text-white');
    clickedBtn.classList.remove('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
    clickedBtn.style.backgroundColor = '#102C46';
    clickedBtn.style.borderColor = '#102C46';
    
    activeLevel = clickedBtn.dataset.level;
    
    // Sync with other filter section
    const otherSelector = selector === '.level-btn' ? '.level-btn-mobile' : '.level-btn';
    document.querySelectorAll(otherSelector).forEach(b => {
        if (b.dataset.level === activeLevel) {
            b.classList.add('active', 'text-white');
            b.classList.remove('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
            b.style.backgroundColor = '#102C46';
            b.style.borderColor = '#102C46';
        } else {
            b.classList.remove('active', 'text-white');
            b.classList.add('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
            b.style.backgroundColor = '';
            b.style.borderColor = '';
        }
    });
    
    applyFilters();
}

function handleCountryButtonClick(clickedBtn, selector) {
    // Remove active class from all country buttons in this group
    document.querySelectorAll(selector).forEach(b => {
        b.classList.remove('active', 'text-white');
        b.classList.add('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
        b.style.backgroundColor = '';
        b.style.borderColor = '';
    });
    
    // Add active class to clicked button
    clickedBtn.classList.add('active', 'text-white');
    clickedBtn.classList.remove('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
    clickedBtn.style.backgroundColor = '#102C46';
    clickedBtn.style.borderColor = '#102C46';
    
    activeCountry = clickedBtn.dataset.country;
    
    // Sync with other filter section
    const otherSelector = selector === '.country-btn' ? '.country-btn-mobile' : '.country-btn';
    document.querySelectorAll(otherSelector).forEach(b => {
        if (b.dataset.country === activeCountry) {
            b.classList.add('active', 'text-white');
            b.classList.remove('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
            b.style.backgroundColor = '#102C46';
            b.style.borderColor = '#102C46';
        } else {
            b.classList.remove('active', 'text-white');
            b.classList.add('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
            b.style.backgroundColor = '';
            b.style.borderColor = '';
        }
    });
    
    applyFilters();
}

function loadUniversities() {
    // Try to get search value from either desktop or mobile input
    const searchInput = document.getElementById('filter-name');
    const searchInputMobile = document.getElementById('filter-name-mobile');
    
    console.log('Desktop input:', searchInput, 'value:', searchInput?.value);
    console.log('Mobile input:', searchInputMobile, 'value:', searchInputMobile?.value);
    
    // Get the value, handling undefined and empty strings properly
    let searchValue = '';
    if (searchInput && typeof searchInput.value === 'string' && searchInput.value.trim()) {
        searchValue = searchInput.value.trim();
    } else if (searchInputMobile && typeof searchInputMobile.value === 'string' && searchInputMobile.value.trim()) {
        searchValue = searchInputMobile.value.trim();
    }
    
    console.log('Final search value:', searchValue);
    console.log('Active country:', activeCountry, 'Active level:', activeLevel);
    
    const params = new URLSearchParams({
        name: searchValue,
        country: activeCountry,
        level: activeLevel,
    });
    
    console.log('API call params:', params.toString());
    
    fetch(`/api/universities/?${params}`)
        .then(response => response.json())
        .then(data => {
            console.log('API response:', data);
            allUniversities = data.universities;
            filteredUniversities = [...allUniversities];
            renderUniversities();
            updateCount(data.count);
        })
        .catch(error => {
            console.error('Error loading universities:', error);
            const grid = document.getElementById('universities-grid');
            if (grid) {
                grid.innerHTML = '<p class="text-center text-gray-500 dark:text-gray-400 py-8">Failed to load universities. Please try again.</p>';
            }
        });
}

function applyFilters() {
    loadUniversities();
}

function applySorting() {
    const sortBy = document.getElementById('sort-by');
    const sortValue = sortBy ? sortBy.value : 'name-asc';
    
    switch(sortValue) {
        case 'name-asc':
            filteredUniversities.sort((a, b) => a.name.localeCompare(b.name));
            break;
        case 'name-desc':
            filteredUniversities.sort((a, b) => b.name.localeCompare(a.name));
            break;
        case 'tuition-asc':
            filteredUniversities.sort((a, b) => {
                const aFee = activeLevel === 'UG' ? (a.avg_ug_tuition || 0) : (a.avg_pg_tuition || 0);
                const bFee = activeLevel === 'UG' ? (b.avg_ug_tuition || 0) : (b.avg_pg_tuition || 0);
                return aFee - bFee;
            });
            break;
        case 'tuition-desc':
            filteredUniversities.sort((a, b) => {
                const aFee = activeLevel === 'UG' ? (a.avg_ug_tuition || 0) : (a.avg_pg_tuition || 0);
                const bFee = activeLevel === 'UG' ? (b.avg_ug_tuition || 0) : (b.avg_pg_tuition || 0);
                return bFee - aFee;
            });
            break;
        case 'ranking':
            filteredUniversities.sort((a, b) => {
                const aRank = a.ranking || 9999;
                const bRank = b.ranking || 9999;
                return aRank - bRank;
            });
            break;
    }
    
    renderUniversities();
}

function renderUniversities() {
    const grid = document.getElementById('universities-grid');
    const loadMoreContainer = document.getElementById('load-more-container');
    if (!grid) return;
    
    if (filteredUniversities.length === 0) {
        grid.innerHTML = `
            <div class="col-span-full text-center py-12">
                <p class="text-gray-500 dark:text-gray-400 text-lg">No universities found matching your criteria.</p>
                <button onclick="clearFilters()" class="mt-4 hover:underline" style="color: #102C46;">Clear all filters</button>
            </div>
        `;
        if (loadMoreContainer) loadMoreContainer.classList.add('hidden');
        return;
    }
    
    // Reset pagination
    currentPage = 0;
    displayedUniversities = [];
    grid.innerHTML = '';
    
    // Load first page
    loadMoreUniversities();
}

function loadMoreUniversities() {
    const grid = document.getElementById('universities-grid');
    const loadMoreContainer = document.getElementById('load-more-container');
    const loadMoreBtn = document.getElementById('load-more-btn');
    const loadMoreText = document.getElementById('load-more-text');
    const loadMoreSpinner = document.getElementById('load-more-spinner');
    
    if (!grid) return;
    
    // Show loading state
    if (loadMoreBtn) {
        loadMoreBtn.disabled = true;
        if (loadMoreText) loadMoreText.textContent = 'Loading...';
        if (loadMoreSpinner) loadMoreSpinner.classList.remove('hidden');
    }
    
    // Simulate loading delay
    setTimeout(() => {
        const startIndex = currentPage * UNIVERSITIES_PER_PAGE;
        const endIndex = startIndex + UNIVERSITIES_PER_PAGE;
        const newUniversities = filteredUniversities.slice(startIndex, endIndex);
        
        // Append new universities
        newUniversities.forEach(uni => {
            const cardHTML = createUniversityCard(uni);
            grid.insertAdjacentHTML('beforeend', cardHTML);
            displayedUniversities.push(uni);
        });
        
        // Initialize favorite buttons for newly added cards
        initializeFavoriteButtons();
        
        currentPage++;
        
        // Show/hide load more button
        const hasMore = displayedUniversities.length < filteredUniversities.length;
        if (loadMoreContainer) {
            if (hasMore) {
                loadMoreContainer.classList.remove('hidden');
            } else {
                loadMoreContainer.classList.add('hidden');
            }
        }
        
        // Reset button state
        if (loadMoreBtn) {
            loadMoreBtn.disabled = false;
            if (loadMoreText) loadMoreText.textContent = 'Load More';
            if (loadMoreSpinner) loadMoreSpinner.classList.add('hidden');
        }
    }, 300);
}

function createUniversityCard(uni) {
    const isFavorite = isUniversityFavorite(uni.university_id);
    
    return `
        <div class="bg-white/10 backdrop-blur-md border border-gray-200/30 rounded-lg shadow-lg hover:shadow-xl transition-all duration-300">
            <div class="p-8">
                <!-- Desktop Layout -->
                <div class="hidden lg:flex items-start gap-6">
                    <!-- University Thumbnail -->
                    <a href="/universities/${uni.slug}/" class="w-16 h-16 bg-gray-100 dark:bg-gray-700 rounded-lg flex items-center justify-center flex-shrink-0 hover:opacity-80 transition-opacity">
                        ${uni.logo 
                            ? `<img src="${uni.logo}" alt="${uni.name}" class="w-full h-full object-contain rounded-lg">` 
                            : `<svg class="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"></path>
                               </svg>`
                        }
                    </a>
                    
                    <!-- University Info -->
                    <div class="flex-1">
                        <a href="/universities/${uni.slug}/" class="hover:opacity-80 transition-opacity">
                            <h3 class="text-xl font-bold text-gray-900 dark:text-white mb-2">${uni.name}</h3>
                        </a>
                        <p class="text-base text-gray-600 dark:text-gray-400">${uni.city}, ${uni.country === 'UK' ? 'United Kingdom' : 'Ireland'}</p>
                    </div>
                    
                    <!-- Favorite Button -->
                    <button class="favorite-btn p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex-shrink-0"
                            data-university-id="${uni.university_id}"
                            title="${isFavorite ? 'Remove from favorites' : 'Add to favorites'}">
                        <svg class="w-6 h-6 ${isFavorite ? 'text-red-500 fill-current' : 'text-gray-400'}" 
                             fill="${isFavorite ? 'currentColor' : 'none'}" 
                             stroke="currentColor" 
                             viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" 
                                  d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z"></path>
                        </svg>
                    </button>
                </div>
                
                <!-- Mobile Layout -->
                <div class="lg:hidden">
                    <div class="flex items-start justify-between mb-4">
                        <!-- University Thumbnail -->
                        <a href="/universities/${uni.slug}/" class="w-16 h-16 bg-gray-100 dark:bg-gray-700 rounded-lg flex items-center justify-center flex-shrink-0 hover:opacity-80 transition-opacity">
                            ${uni.logo 
                                ? `<img src="${uni.logo}" alt="${uni.name}" class="w-full h-full object-contain rounded-lg">` 
                                : `<svg class="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"></path>
                                   </svg>`
                            }
                        </a>
                        
                        <!-- Favorite Button -->
                        <button class="favorite-btn p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex-shrink-0"
                                data-university-id="${uni.university_id}"
                                title="${isFavorite ? 'Remove from favorites' : 'Add to favorites'}">
                            <svg class="w-6 h-6 ${isFavorite ? 'text-red-500 fill-current' : 'text-gray-400'}" 
                                 fill="${isFavorite ? 'currentColor' : 'none'}" 
                                 stroke="currentColor" 
                                 viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" 
                                      d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z"></path>
                            </svg>
                        </button>
                    </div>
                    
                    <!-- University Info below thumbnail -->
                    <div>
                        <a href="/universities/${uni.slug}/" class="hover:opacity-80 transition-opacity">
                            <h3 class="text-xl font-bold text-gray-900 dark:text-white mb-2">${uni.name}</h3>
                        </a>
                        <p class="text-base text-gray-600 dark:text-gray-400">${uni.city}, ${uni.country === 'UK' ? 'United Kingdom' : 'Ireland'}</p>
                    </div>
                </div>
            </div>
            
            <!-- Horizontal Divider with padding -->
            <div class="px-8">
                <hr class="border-gray-300 dark:border-gray-600">
            </div>
            
            <!-- Action Buttons -->
            <div class="p-8 pt-6">
                <!-- Desktop Buttons -->
                <div class="hidden lg:flex gap-6">
                    <a href="/universities/${uni.slug}/" 
                       class="flex-1 px-10 py-4 text-white text-center rounded-lg hover:opacity-90 transition-all text-base font-semibold"
                       style="background-color: #102C46;">
                        View Details
                    </a>
                    <a href="${uni.website.startsWith('http') ? uni.website : 'https://' + uni.website}" 
                       target="_blank" 
                       rel="noopener noreferrer"
                       class="flex-1 px-10 py-4 text-white text-center rounded-lg transition-all text-base font-semibold"
                       style="background-color: #1e40af;">
                        Visit Website
                    </a>
                </div>
                
                <!-- Mobile Buttons -->
                <div class="lg:hidden flex gap-4">
                    <a href="/universities/${uni.slug}/" 
                       class="flex-1 px-6 py-3 text-white text-center rounded-lg hover:opacity-90 transition-all text-sm font-semibold"
                       style="background-color: #102C46;">
                        Details
                    </a>
                    <a href="${uni.website.startsWith('http') ? uni.website : 'https://' + uni.website}" 
                       target="_blank" 
                       rel="noopener noreferrer"
                       class="flex-1 px-6 py-3 text-white text-center rounded-lg transition-all text-sm font-semibold"
                       style="background-color: #1e40af;">
                        Website
                    </a>
                </div>
            </div>
        </div>
    `;
}

function updateCount(count) {
    const countElement = document.getElementById('university-count');
    if (countElement) {
        const countryName = activeCountry === 'IE' ? 'Ireland' : 'UK';
        countElement.textContent = `${count} ${countryName} ${count === 1 ? 'University' : 'Universities'}`;
    }
}

function clearFilters() {
    // Clear search inputs
    const searchInput = document.getElementById('filter-name');
    const searchInputMobile = document.getElementById('filter-name-mobile');
    if (searchInput) searchInput.value = '';
    if (searchInputMobile) searchInputMobile.value = '';
    
    activeLevel = 'UG';
    activeCountry = 'IE';
    
    // Reset level buttons - Desktop
    document.querySelectorAll('.level-btn').forEach(btn => {
        btn.classList.remove('active', 'text-white');
        btn.classList.add('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
        btn.style.backgroundColor = '';
        btn.style.borderColor = '';
        
        if (btn.dataset.level === 'UG') {
            btn.classList.add('active', 'text-white');
            btn.classList.remove('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
            btn.style.backgroundColor = '#102C46';
            btn.style.borderColor = '#102C46';
        }
    });
    
    // Reset level buttons - Mobile
    document.querySelectorAll('.level-btn-mobile').forEach(btn => {
        btn.classList.remove('active', 'text-white');
        btn.classList.add('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
        btn.style.backgroundColor = '';
        btn.style.borderColor = '';
        
        if (btn.dataset.level === 'UG') {
            btn.classList.add('active', 'text-white');
            btn.classList.remove('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
            btn.style.backgroundColor = '#102C46';
            btn.style.borderColor = '#102C46';
        }
    });
    
    // Reset country buttons - Desktop
    document.querySelectorAll('.country-btn').forEach(btn => {
        btn.classList.remove('active', 'text-white');
        btn.classList.add('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
        btn.style.backgroundColor = '';
        btn.style.borderColor = '';
        
        if (btn.dataset.country === 'IE') {
            btn.classList.add('active', 'text-white');
            btn.classList.remove('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
            btn.style.backgroundColor = '#102C46';
            btn.style.borderColor = '#102C46';
        }
    });
    
    // Reset country buttons - Mobile
    document.querySelectorAll('.country-btn-mobile').forEach(btn => {
        btn.classList.remove('active', 'text-white');
        btn.classList.add('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
        btn.style.backgroundColor = '';
        btn.style.borderColor = '';
        
        if (btn.dataset.country === 'IE') {
            btn.classList.add('active', 'text-white');
            btn.classList.remove('bg-white', 'dark:bg-dark_input', 'text-gray-700', 'dark:text-gray-300', 'border-gray-300', 'dark:border-gray-600');
            btn.style.backgroundColor = '#102C46';
            btn.style.borderColor = '#102C46';
        }
    });
    
    loadUniversities();
}

// Favorites functionality
function initializeFavoriteButtons() {
    document.querySelectorAll('.favorite-btn').forEach(btn => {
        // Remove any existing listeners by cloning
        const newBtn = btn.cloneNode(true);
        btn.parentNode.replaceChild(newBtn, btn);
        
        newBtn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            const universityId = this.dataset.universityId;
            toggleFavorite(universityId, this);
        });
    });
}

function isUniversityFavorite(universityId) {
    const favorites = JSON.parse(localStorage.getItem('universityFavorites') || '[]');
    return favorites.some(fav => fav.university_id === universityId);
}

function toggleFavorite(universityId, btn) {
    if (!btn) {
        btn = document.querySelector(`.favorite-btn[data-university-id="${universityId}"]`);
    }
    if (!btn) return;
    
    // Find the card - try multiple selectors
    const card = btn.closest('.bg-white') || 
                 btn.closest('.bg-white\\/10') || 
                 btn.closest('[class*="bg-white"]') ||
                 btn.closest('div[class*="backdrop-blur"]');
    
    if (!card) {
        console.error('Could not find card element');
        return;
    }
    
    // Extract university data
    const universityData = {
        university_id: universityId,
        type: 'university'
    };
    
    // Get name
    const nameEl = card.querySelector('h3') || card.querySelector('h2');
    universityData.name = nameEl ? nameEl.textContent.trim() : 'University';
    
    // Get slug
    const link = card.querySelector('a[href*="/universities/"]');
    if (link) {
        const href = link.getAttribute('href');
        const slugMatch = href.match(/\/universities\/([^\/]+)/);
        universityData.slug = slugMatch ? slugMatch[1] : universityId;
    } else {
        universityData.slug = universityId;
    }
    
    // Get location
    const locationText = card.querySelector('.text-gray-600')?.textContent || '';
    const parts = locationText.split(',');
    universityData.city = parts[0]?.trim() || '';
    universityData.country = parts[1]?.trim() || '';
    
    // Get banner image
    const banner = card.querySelector('img');
    if (banner) {
        universityData.banner = banner.src;
    }
    
    let favorites = JSON.parse(localStorage.getItem('universityFavorites') || '[]');
    const index = favorites.findIndex(fav => fav.university_id === universityId);
    
    if (index > -1) {
        favorites.splice(index, 1);
        showToast('Removed from favorites', 'success');
    } else {
        favorites.push(universityData);
        showToast('Added to favorites', 'success');
    }
    
    localStorage.setItem('universityFavorites', JSON.stringify(favorites));
    
    // Update the button appearance
    if (btn) {
        const svg = btn.querySelector('svg');
        const isFav = index === -1; // Will be favorite after toggle
        
        if (isFav) {
            svg.classList.add('text-red-500', 'fill-current');
            svg.classList.remove('text-gray-400');
            svg.setAttribute('fill', 'currentColor');
            btn.title = 'Remove from favorites';
        } else {
            svg.classList.remove('text-red-500', 'fill-current');
            svg.classList.add('text-gray-400');
            svg.setAttribute('fill', 'none');
            btn.title = 'Add to favorites';
        }
    }
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `fixed bottom-4 right-4 px-6 py-3 rounded-lg shadow-lg z-50 ${
        type === 'success' ? 'bg-green-500' : 
        type === 'error' ? 'bg-red-500' : 'bg-blue-500'
    } text-white`;
    toast.textContent = message;
    
    document.body.appendChild(toast);
    
    setTimeout(() => {
        toast.remove();
    }, 3000);
}

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
