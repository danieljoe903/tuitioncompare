// Scholarships page functionality
let scholarshipsData = [];
let universitiesData = {};
let currentDisplayCount = 20;
let filteredScholarships = [];
let isProgressiveLoading = false;

// Load scholarships data
async function loadScholarships() {
    showLoading();
    isProgressiveLoading = true;
    
    try {
        scholarshipsData = [];
        let loadedCount = 0;
        let firstBatchLoaded = false;
        
        // Fetch universities first
        const universitiesResponse = await fetch('/api/universities/');
        
        if (!universitiesResponse.ok) {
            throw new Error('Failed to fetch universities');
        }
        
        const universitiesData = await universitiesResponse.json();
        
        if (!universitiesData.universities || universitiesData.universities.length === 0) {
            throw new Error('No universities found');
        }
        
        const totalUniversities = universitiesData.universities.length;
        
        // Fetch scholarships for each university - stream results as they arrive
        const fetchPromises = universitiesData.universities.map(university => 
            fetch(`/api/scholarships/?university_id=${university.university_id}`)
                .then(res => res.ok ? res.json() : { scholarships: [] })
                .then(data => {
                    if (data.scholarships && Array.isArray(data.scholarships) && data.scholarships.length > 0) {
                        scholarshipsData.push(...data.scholarships);
                        loadedCount++;
                        
                        // Show first batch immediately after first successful fetch
                        if (!firstBatchLoaded) {
                            firstBatchLoaded = true;
                            hideLoading();
                            displayScholarships(scholarshipsData, false, true);
                        } else {
                            // Update display with new scholarships as they arrive
                            displayScholarships(scholarshipsData, false, false);
                        }
                        
                        // Update loading message
                        updateLoadingProgress(loadedCount, totalUniversities);
                    }
                })
                .catch(err => {
                    console.error(`Error fetching scholarships for ${university.university_id}:`, err);
                    return { scholarships: [] };
                })
        );
        
        // Wait for all fetches to complete
        await Promise.all(fetchPromises);
        
        console.log(`Loaded ${scholarshipsData.length} scholarships from ${loadedCount} universities`);
        
        isProgressiveLoading = false;
        
        // Final display
        if (scholarshipsData.length > 0) {
            hideLoading();
            populateFilters();
            displayScholarships(scholarshipsData);
        } else {
            hideLoading();
            showNoResults();
        }
        
    } catch (error) {
        console.error('Error loading scholarships:', error);
        isProgressiveLoading = false;
        hideLoading();
        showError('Failed to load scholarships. Please refresh the page.');
    }
}

// Parse CSV line handling commas in quotes
function parseCSVLine(line) {
    const values = [];
    let current = '';
    let inQuotes = false;
    
    for (let i = 0; i < line.length; i++) {
        const char = line[i];
        
        if (char === '"') {
            inQuotes = !inQuotes;
        } else if (char === ',' && !inQuotes) {
            values.push(current.trim());
            current = '';
        } else {
            current += char;
        }
    }
    values.push(current.trim());
    
    return values;
}

// Load universities data
async function loadUniversities() {
    try {
        const response = await fetch('/static/data/universities.csv');
        const text = await response.text();
        
        const lines = text.split('\n');
        const headers = lines[0].split(',').map(h => h.trim());
        
        lines.slice(1)
            .filter(line => line.trim())
            .forEach(line => {
                const values = parseCSVLine(line);
                const uni = {};
                headers.forEach((header, index) => {
                    uni[header] = values[index] || '';
                });
                if (uni.university_id) {
                    universitiesData[uni.university_id] = {
                        name: uni.name,
                        country: uni.country
                    };
                }
            });
        
        console.log(`Loaded ${Object.keys(universitiesData).length} universities`);
    } catch (error) {
        console.error('Error loading universities:', error);
    }
}

// Populate filter dropdowns
function populateFilters() {
    // Location filter is now static with UK and Ireland options
    // No dynamic population needed
}

// Display scholarships
function displayScholarships(scholarships, append = false, firstBatch = false) {
    const grid = document.getElementById('scholarships-grid');
    const noResults = document.getElementById('no-results');
    const resultsCount = document.getElementById('results-count');
    const loadMoreContainer = document.getElementById('load-more-container');
    
    if (!grid) return;
    
    // Store filtered scholarships for Load More
    if (!append && !firstBatch) {
        filteredScholarships = scholarships;
        currentDisplayCount = 20;
        grid.innerHTML = '';
    }
    
    if (scholarships.length === 0) {
        grid.classList.add('hidden');
        noResults.classList.remove('hidden');
        resultsCount.textContent = '0';
        if (loadMoreContainer) loadMoreContainer.classList.add('hidden');
        return;
    }
    
    grid.classList.remove('hidden');
    noResults.classList.add('hidden');
    resultsCount.textContent = scholarships.length;
    
    // For progressive loading, show all current scholarships
    if (firstBatch) {
        filteredScholarships = scholarships;
        currentDisplayCount = Math.min(20, scholarships.length);
        grid.innerHTML = '';
        const toDisplay = scholarships.slice(0, currentDisplayCount);
        toDisplay.forEach((scholarship, index) => {
            const card = createScholarshipCard(scholarship, index);
            grid.appendChild(card);
        });
        // Refresh AOS for new elements
        if (typeof AOS !== 'undefined') {
            AOS.refresh();
        }
    } else if (!append) {
        // Normal display (after filtering or complete load)
        const toDisplay = scholarships.slice(0, currentDisplayCount);
        grid.innerHTML = '';
        toDisplay.forEach((scholarship, index) => {
            const card = createScholarshipCard(scholarship, index);
            grid.appendChild(card);
        });
        // Refresh AOS for new elements
        if (typeof AOS !== 'undefined') {
            AOS.refresh();
        }
    } else {
        // Append more (Load More button)
        const toDisplay = scholarships.slice(currentDisplayCount - 20, currentDisplayCount);
        toDisplay.forEach((scholarship, index) => {
            const actualIndex = currentDisplayCount - 20 + index;
            const card = createScholarshipCard(scholarship, actualIndex);
            grid.appendChild(card);
        });
        // Refresh AOS for new elements
        if (typeof AOS !== 'undefined') {
            AOS.refresh();
        }
    }
    
    // Show/hide Load More button
    if (loadMoreContainer) {
        if (currentDisplayCount < scholarships.length) {
            loadMoreContainer.classList.remove('hidden');
        } else {
            loadMoreContainer.classList.add('hidden');
        }
    }
}

// Create scholarship card
function createScholarshipCard(scholarship, index) {
    const card = document.createElement('div');
    card.className = 'bg-white/80 backdrop-blur-md dark:bg-dark_card/80 rounded-22 p-6 shadow-round-box';
    // Only add AOS animation when not progressively loading
    if (!isProgressiveLoading) {
        card.setAttribute('data-aos', 'fade-up');
        card.setAttribute('data-aos-delay', (index % 4) * 100);
    }
    
    // Get university name from scholarship data
    const universityName = scholarship.university_name || 'Various Universities';
    
    // Format award value
    const awardValue = formatAwardValue(scholarship.award_value, scholarship.currency);
    
    // Format deadline
    const deadline = formatDate(scholarship.deadline);
    
    // Determine status badge
    const statusBadge = getStatusBadge(scholarship.deadline);
    
    // Parse level
    const level = formatLevel(scholarship.level);
    
    // Parse requirements
    const requirements = scholarship.requirements ? scholarship.requirements.split(';').filter(r => r.trim()) : [];
    
    card.innerHTML = `
        <div class="flex justify-between items-start mb-4">
            <h3 class="text-22 font-bold pr-4">${scholarship.name}</h3>
            ${statusBadge}
        </div>
        
        <div class="mb-3 text-sm text-primary font-semibold">
            ${universityName}
        </div>
        
        <div class="space-y-2 mb-4">
            <p class="text-SlateBlueText dark:text-opacity-80">
                <strong>Award:</strong> ${awardValue}
            </p>
            <p class="text-SlateBlueText dark:text-opacity-80">
                <strong>Award Type:</strong> ${scholarship.award_type}
            </p>
            <p class="text-SlateBlueText dark:text-opacity-80">
                <strong>Deadline:</strong> ${deadline}
            </p>
            <p class="text-SlateBlueText dark:text-opacity-80">
                <strong>Level:</strong> ${level}
            </p>
            <p class="text-SlateBlueText dark:text-opacity-80">
                <strong>Eligibility:</strong> ${scholarship.eligibility}
            </p>
        </div>
        
        ${scholarship.description ? `
        <div class="mb-4">
            <p class="text-sm text-SlateBlueText dark:text-opacity-80 line-clamp-3">
                ${scholarship.description}
            </p>
        </div>
        ` : ''}
        
        ${requirements.length > 0 ? `
        <div class="mb-4">
            <h4 class="font-bold mb-2 text-sm">Requirements:</h4>
            <ul class="list-disc list-inside text-sm text-SlateBlueText dark:text-opacity-80 space-y-1">
                ${requirements.slice(0, 3).map(req => `<li>${req.trim()}</li>`).join('')}
                ${requirements.length > 3 ? '<li class="text-gray-500">+ more...</li>' : ''}
            </ul>
        </div>
        ` : ''}
        
        ${scholarship.url ? `
        <a href="${scholarship.url.startsWith('http') ? scholarship.url : 'https://' + scholarship.url}" target="_blank" rel="noopener noreferrer" class="btn btn-1 hover-filled-slide-down rounded-lg overflow-hidden w-full block text-center">
            <span>Visit Website</span>
        </a>
        ` : `
        <button class="btn btn-1 hover-filled-slide-down rounded-lg overflow-hidden w-full" disabled>
            <span>Contact University</span>
        </button>
        `}
    `;
    
    return card;
}

// Format award value
function formatAwardValue(value, currency) {
    if (!value) return 'Contact for details';
    
    const symbol = currency === 'EUR' ? '€' : '£';
    
    // Handle ranges
    if (value.includes('-')) {
        const [min, max] = value.split('-').map(v => parseFloat(v.trim()));
        return `${symbol}${min.toLocaleString()} - ${symbol}${max.toLocaleString()}`;
    }
    
    const amount = parseFloat(value);
    if (isNaN(amount)) return value;
    
    return `${symbol}${amount.toLocaleString()}`;
}

// Format date
function formatDate(dateString) {
    if (!dateString) return 'Rolling basis';
    
    try {
        const date = new Date(dateString);
        return date.toLocaleDateString('en-GB', { 
            day: 'numeric', 
            month: 'long', 
            year: 'numeric' 
        });
    } catch {
        return dateString;
    }
}

// Get status badge based on deadline
function getStatusBadge(deadline) {
    if (!deadline) {
        return '<span class="px-3 py-1 bg-Aquamarine text-green-900 dark:bg-Aquamarine dark:text-green-900 rounded-full text-sm font-medium">Open</span>';
    }
    
    try {
        const deadlineDate = new Date(deadline);
        const today = new Date();
        const daysUntil = Math.ceil((deadlineDate - today) / (1000 * 60 * 60 * 24));
        
        if (daysUntil < 0) {
            return '<span class="px-3 py-1 bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-300 rounded-full text-sm font-medium">Closed</span>';
        } else if (daysUntil <= 30) {
            return '<span class="px-4 py-1 bg-LightYellow text-OliveDrab dark:bg-yellow-900 dark:text-yellow-100 rounded-full text-sm font-medium whitespace-nowrap">Closing Soon</span>';
        } else {
            return '<span class="px-3 py-1 bg-Aquamarine text-green-900 dark:bg-Aquamarine dark:text-green-900 rounded-full text-sm font-medium">Open</span>';
        }
    } catch {
        return '<span class="px-3 py-1 bg-Aquamarine text-green-900 dark:bg-Aquamarine dark:text-green-900 rounded-full text-sm font-medium">Open</span>';
    }
}

// Format level
function formatLevel(level) {
    const levels = {
        'UG': 'Undergraduate',
        'PG': 'Postgraduate',
        'PhD': 'PhD/Doctorate'
    };
    return levels[level] || level;
}

// Show loading state
function showLoading() {
    const spinner = document.getElementById('loading-spinner');
    const resultsSection = document.getElementById('results-section');
    const grid = document.getElementById('scholarships-grid');
    const noResults = document.getElementById('no-results');
    
    if (spinner) {
        spinner.classList.remove('hidden');
        // Update message for progressive loading
        const message = spinner.querySelector('p');
        if (message) {
            message.textContent = 'Loading scholarships...';
        }
    }
    if (resultsSection) resultsSection.classList.add('hidden');
    if (grid) grid.classList.add('hidden');
    if (noResults) noResults.classList.add('hidden');
}

// Update loading progress message
function updateLoadingProgress(loaded, total) {
    const spinner = document.getElementById('loading-spinner');
    if (spinner && !spinner.classList.contains('hidden')) {
        const message = spinner.querySelector('p');
        if (message) {
            message.textContent = `Loading scholarships... (${loaded}/${total} sources)`;
        }
    }
}

// Hide loading state
function hideLoading() {
    const spinner = document.getElementById('loading-spinner');
    const resultsSection = document.getElementById('results-section');
    
    if (spinner) spinner.classList.add('hidden');
    if (resultsSection) resultsSection.classList.remove('hidden');
}

// Show error message
function showError(message) {
    const grid = document.getElementById('scholarships-grid');
    const noResults = document.getElementById('no-results');
    const resultsSection = document.getElementById('results-section');
    
    if (grid) grid.classList.add('hidden');
    if (resultsSection) resultsSection.classList.add('hidden');
    if (noResults) {
        noResults.innerHTML = `
            <svg class="w-24 h-24 mx-auto text-red-400 dark:text-red-600 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
            <h3 class="text-2xl font-bold text-gray-700 dark:text-gray-300 mb-2">Error Loading Scholarships</h3>
            <p class="text-gray-600 dark:text-gray-400">${message}</p>
        `;
        noResults.classList.remove('hidden');
    }
}

// Show no results message
function showNoResults() {
    const grid = document.getElementById('scholarships-grid');
    const noResults = document.getElementById('no-results');
    const resultsCount = document.getElementById('results-count');
    const resultsSection = document.getElementById('results-section');
    
    if (grid) grid.classList.add('hidden');
    if (resultsSection) resultsSection.classList.remove('hidden');
    if (noResults) {
        noResults.innerHTML = `
            <svg class="w-24 h-24 mx-auto text-gray-400 dark:text-gray-600 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
            <h3 class="text-2xl font-bold text-gray-700 dark:text-gray-300 mb-2">No Scholarships Found</h3>
            <p class="text-gray-600 dark:text-gray-400">Try adjusting your filters or search terms</p>
        `;
        noResults.classList.remove('hidden');
    }
    if (resultsCount) resultsCount.textContent = '0';
}

// Filter scholarships
function filterScholarships() {
    const levelFilter = document.getElementById('level-filter').value;
    const locationFilter = document.getElementById('location-filter').value;
    const searchQuery = document.getElementById('scholarship-search').value.toLowerCase();
    
    let filtered = scholarshipsData;
    
    // Filter by level
    if (levelFilter) {
        filtered = filtered.filter(s => s.level === levelFilter);
    }
    
    // Filter by location (country)
    if (locationFilter) {
        filtered = filtered.filter(s => {
            if (!s.university_country) return false;
            
            if (locationFilter === 'UK') {
                return s.university_country === 'UK';
            } else if (locationFilter === 'IE') {
                return s.university_country === 'IE';
            }
            return false;
        });
    }
    
    // Filter by search query
    if (searchQuery) {
        filtered = filtered.filter(s => {
            const uniName = s.university_name || '';
            const searchText = `${s.name} ${s.description} ${s.eligibility} ${uniName}`.toLowerCase();
            return searchText.includes(searchQuery);
        });
    }
    
    displayScholarships(filtered);
}

// Event listeners
document.addEventListener('DOMContentLoaded', function() {
    // Load scholarships on page load
    loadScholarships();
    
    // Setup filter event listeners
    const levelFilter = document.getElementById('level-filter');
    const locationFilter = document.getElementById('location-filter');
    const searchInput = document.getElementById('scholarship-search');
    
    if (levelFilter) {
        levelFilter.addEventListener('change', filterScholarships);
    }
    
    if (locationFilter) {
        locationFilter.addEventListener('change', filterScholarships);
    }
    
    if (searchInput) {
        searchInput.addEventListener('input', debounce(filterScholarships, 300));
    }
    
    // Load More button
    const loadMoreBtn = document.getElementById('load-more-btn');
    if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', function() {
            currentDisplayCount += 20;
            displayScholarships(filteredScholarships, true);
        });
    }
});

// Debounce function for search
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
