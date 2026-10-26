// Compare Tuition Fee Functionality

let allResults = [];
let exchangeRates = {};
let currencySymbols = {};
let userCurrency = 'GBP';
let userCurrencySymbol = '£';
let debounceTimer;
let allResultsUnfiltered = []; // Store original results before filtering

// Fetch exchange rates from the API on page load
async function loadExchangeRates() {
    try {
        const response = await fetch('/api/exchange-rates/');
        const data = await response.json();
        exchangeRates = data.rates;
        currencySymbols = data.symbols;
        
        // Ensure GBP is always in the rates
        if (!exchangeRates['GBP']) {
            exchangeRates['GBP'] = 1;
            currencySymbols['GBP'] = '£';
        }
        
        console.log('Exchange rates loaded:', exchangeRates);
    } catch (error) {
        console.error('Error loading exchange rates:', error);
        // Fallback to default rates if API fails
        exchangeRates = {
            'GBP': 1,
            'EUR': 1.17,
            'NGN': 1850,
            'GHS': 15.5,
            'KES': 190,
            'ZAR': 23,
            'USD': 1.27,
            'INR': 106,
            'CNY': 9.2,
            'PKR': 353,
            'BDT': 140,
        };
        currencySymbols = {
            'GBP': '£',
            'EUR': '€',
            'NGN': '₦',
            'GHS': '₵',
            'KES': 'KSh',
            'ZAR': 'R',
            'USD': '$',
            'INR': '₹',
            'CNY': '¥',
            'PKR': 'Rs',
            'BDT': '৳',
        };
    }
}

document.addEventListener('DOMContentLoaded', function() {
    // Load exchange rates first
    loadExchangeRates().then(() => {
        // Setup form submission
        const form = document.getElementById('tuition-compare-form');
        if (form) {
            form.addEventListener('submit', handleFormSubmit);
        }
        
        // Setup level filter dropdown
        const levelFilter = document.getElementById('level-filter');
        if (levelFilter) {
            levelFilter.addEventListener('change', function() {
                filterResultsByLevel(this.value);
            });
        }
        
        // Setup level filter search button
        const levelSearchBtn = document.getElementById('level-search-btn');
        if (levelSearchBtn && levelFilter) {
            levelSearchBtn.addEventListener('click', function() {
                const selectedLevel = levelFilter.value;
                filterResultsByLevel(selectedLevel);
            });
        }
        
        // Track residence country change for currency conversion
        const residenceSelect = document.getElementById('residence-country');
        if (residenceSelect) {
            residenceSelect.addEventListener('change', function() {
                updateUserCurrency(this.value);
            });
        }
        
        // Setup subject autocomplete
        setupSubjectAutocomplete();
    });
});

// Setup subject autocomplete
function setupSubjectAutocomplete() {
    const subjectInput = document.getElementById('subject');
    const suggestionsDiv = document.getElementById('subject-suggestions');
    
    if (!subjectInput || !suggestionsDiv) return;
    
    // Input event with debounce for autocomplete
    subjectInput.addEventListener('input', function() {
        clearTimeout(debounceTimer);
        const query = this.value.trim();
        
        if (query.length < 2) {
            suggestionsDiv.classList.add('hidden');
            return;
        }
        
        debounceTimer = setTimeout(() => {
            fetchSubjects(query);
        }, 300);
    });
    
    // Hide suggestions when clicking outside
    document.addEventListener('click', function(e) {
        if (!subjectInput.contains(e.target) && !suggestionsDiv.contains(e.target)) {
            suggestionsDiv.classList.add('hidden');
        }
    });
}

// Fetch subjects from API
async function fetchSubjects(query) {
    const suggestionsDiv = document.getElementById('subject-suggestions');
    if (!suggestionsDiv) return;
    
    try {
        const response = await fetch(`/api/subjects/?q=${encodeURIComponent(query)}`);
        const data = await response.json();
        displaySubjectSuggestions(data.subjects);
    } catch (error) {
        console.error('Error fetching subjects:', error);
        suggestionsDiv.classList.add('hidden');
    }
}

// Display subject suggestions
function displaySubjectSuggestions(subjects) {
    const suggestionsDiv = document.getElementById('subject-suggestions');
    if (!suggestionsDiv) return;
    
    if (!subjects || subjects.length === 0) {
        suggestionsDiv.innerHTML = '<div class="px-4 py-3 text-sm text-gray-500 dark:text-gray-400">No subjects found</div>';
        suggestionsDiv.classList.remove('hidden');
        return;
    }
    
    suggestionsDiv.innerHTML = '';
    suggestionsDiv.classList.remove('hidden');
    
    // Create and append suggestion elements
    subjects.forEach(subject => {
        const div = document.createElement('div');
        div.className = 'suggestion-item px-4 py-3 hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer border-b border-gray-200 dark:border-gray-600 last:border-b-0';
        
        const p = document.createElement('p');
        p.className = 'text-sm font-medium text-gray-900 dark:text-white';
        p.textContent = subject.name;
        
        div.appendChild(p);
        
        // Add click event listener
        div.addEventListener('click', function() {
            selectSubject(subject.subject_code, subject.name);
        });
        
        suggestionsDiv.appendChild(div);
    });
}

// Select a subject
function selectSubject(code, name) {
    const subjectInput = document.getElementById('subject');
    const suggestionsDiv = document.getElementById('subject-suggestions');
    
    console.log('Subject selected:', { code, name });
    
    if (subjectInput) {
        subjectInput.value = name;
        subjectInput.dataset.subjectCode = code;
        console.log('Subject input updated:', {
            value: subjectInput.value,
            dataCode: subjectInput.dataset.subjectCode
        });
    }
    
    if (suggestionsDiv) {
        suggestionsDiv.classList.add('hidden');
    }
}

// Update user currency based on residence country
function updateUserCurrency(countryCode) {
    const currencyMap = {
        'NG': 'NGN',
        'GH': 'GHS',
        'KE': 'KES',
        'ZA': 'ZAR',
        'US': 'USD',
        'IN': 'INR',
        'CN': 'CNY',
        'PK': 'PKR',
        'BD': 'BDT',
        'OTHER': 'USD'
    };
    
    userCurrency = currencyMap[countryCode] || 'GBP';
    userCurrencySymbol = currencySymbols[userCurrency] || '$';
}

// Handle form submission
async function handleFormSubmit(e) {
    e.preventDefault();
    
    const formData = new FormData(e.target);
    const residence = formData.get('residence');
    const destination = formData.get('destination');
    const programme = formData.get('programme');
    
    // Get subject search term from input value (allow free text)
    const subjectInput = document.getElementById('subject');
    const subjectSearch = subjectInput.value.trim();
    
    console.log('Form submission:', {
        residence,
        destination,
        programme,
        subjectSearch,
        subjectInputValue: subjectInput.value
    });
    
    if (!subjectSearch) {
        alert('Please enter a subject or course name to search.');
        return;
    }
    
    // Update user currency
    updateUserCurrency(residence);
    
    // Show loading state
    showLoading();
    
    try {
        // Build query params - send the search term instead of subject code
        const params = new URLSearchParams({
            residence: residence,
            destination: destination,
            programme: programme,
            subject: subjectSearch
        });
        
        console.log('API URL:', `/api/compare-tuition/?${params}`);
        
        const response = await fetch(`/api/compare-tuition/?${params}`);
        
        if (!response.ok) {
            throw new Error('Failed to fetch tuition data');
        }
        
        const data = await response.json();
        console.log('API Response:', data);
        console.log('Number of results:', data.results ? data.results.length : 0);
        
        allResults = data.results || [];
        allResultsUnfiltered = [...allResults]; // Store original results
        
        console.log('Stored in allResultsUnfiltered:', allResultsUnfiltered.length);
        console.log('Sample course levels:', allResultsUnfiltered.slice(0, 5).map(r => r.course_level));
        
        // Reset level filter when new search is performed
        const levelFilter = document.getElementById('level-filter');
        if (levelFilter) {
            levelFilter.value = '';
            console.log('Level filter reset to empty');
        }
        
        if (allResults.length === 0) {
            showNoResults();
        } else {
            displayResults(allResults);
        }
        
    } catch (error) {
        console.error('Error fetching tuition data:', error);
        showError('Failed to load tuition comparison. Please try again.');
    }
}

// Show loading state
function showLoading() {
    const resultsSection = document.getElementById('comparison-results');
    const grid = document.getElementById('comparison-grid');
    
    if (resultsSection) {
        resultsSection.classList.remove('hidden');
        resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    
    if (grid) {
        grid.innerHTML = `
            <div class="col-span-full flex justify-center items-center py-12">
                <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-primary"></div>
                <span class="ml-4 text-lg text-gray-600 dark:text-gray-300">Loading comparison...</span>
            </div>
        `;
    }
}

// Show no results message
function showNoResults() {
    const resultsSection = document.getElementById('comparison-results');
    const grid = document.getElementById('comparison-grid');
    const countElement = document.getElementById('comparison-count');
    
    if (resultsSection) {
        resultsSection.classList.remove('hidden');
    }
    
    if (countElement) {
        countElement.textContent = '0 universities found';
    }
    
    if (grid) {
        grid.innerHTML = `
            <div class="col-span-full text-center py-12">
                <svg class="w-24 h-24 mx-auto text-gray-400 dark:text-gray-600 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                </svg>
                <h3 class="text-2xl font-bold text-gray-700 dark:text-gray-300 mb-2">No Results Found</h3>
                <p class="text-gray-600 dark:text-gray-400">No universities found matching your criteria. Try different filters.</p>
            </div>
        `;
    }
}

// Show error message
function showError(message) {
    const resultsSection = document.getElementById('comparison-results');
    const grid = document.getElementById('comparison-grid');
    
    if (resultsSection) {
        resultsSection.classList.remove('hidden');
    }
    
    if (grid) {
        grid.innerHTML = `
            <div class="col-span-full text-center py-12">
                <div class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-6">
                    <svg class="w-12 h-12 mx-auto text-red-500 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                    </svg>
                    <p class="text-red-700 dark:text-red-400">${message}</p>
                </div>
            </div>
        `;
    }
}

// Display results
function displayResults(results) {
    const resultsSection = document.getElementById('comparison-results');
    const grid = document.getElementById('comparison-grid');
    const countElement = document.getElementById('comparison-count');
    
    if (resultsSection) {
        resultsSection.classList.remove('hidden');
    }
    
    if (countElement) {
        countElement.textContent = `${results.length} ${results.length === 1 ? 'university' : 'universities'} found`;
    }
    
    if (grid) {
        grid.innerHTML = results.map((result, index) => createComparisonCard(result, index)).join('');
    }
}

// Create comparison card
function createComparisonCard(result, index) {
    const currency = result.currency || 'GBP';
    const tuitionInGBP = result.tuition_fee;
    const netTuitionInGBP = result.net_tuition;
    
    // Convert to user currency
    const tuitionInUserCurrency = convertCurrency(tuitionInGBP, currency, userCurrency);
    const netTuitionInUserCurrency = convertCurrency(netTuitionInGBP, currency, userCurrency);
    const depositInUserCurrency = result.tuition_deposit > 0 ? convertCurrency(result.tuition_deposit, currency, userCurrency) : 0;
    
    // Format currency
    const tuitionOriginal = formatCurrency(tuitionInGBP, currency);
    const netTuitionOriginal = formatCurrency(netTuitionInGBP, currency);
    const tuitionConverted = formatCurrency(tuitionInUserCurrency, userCurrency);
    const netTuitionConverted = formatCurrency(netTuitionInUserCurrency, userCurrency);
    const depositOriginal = result.tuition_deposit > 0 ? formatCurrency(result.tuition_deposit, currency) : 'Contact University';
    const depositConverted = depositInUserCurrency > 0 ? formatCurrency(depositInUserCurrency, userCurrency) : null;
    
    const savingsAmount = result.scholarship > 0 ? formatCurrency(result.scholarship, currency) : null;
    const savingsPercent = result.scholarship > 0 ? Math.round((result.scholarship / result.tuition_fee) * 100) : 0;
    
    // Use course_id to link to our course detail page
    const courseDetailUrl = `/courses/${result.course_id}/`;
    
    return `
        <div class="bg-white/10 backdrop-blur-md border border-gray-200/30 rounded-lg shadow-lg overflow-hidden transition-all hover:shadow-xl" data-aos="fade-up" data-aos-delay="${(index % 3) * 100}">
            <div class="p-6">
                <!-- Lowest Fee Badge -->
                ${index === 0 ? `
                <div class="mb-3 flex justify-center">
                    <span class="inline-flex items-center px-3 py-1 rounded-full text-sm font-semibold bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200">
                        <svg class="w-4 h-4 mr-1" fill="currentColor" viewBox="0 0 20 20">
                            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/>
                        </svg>
                        Lowest Fee
                    </span>
                </div>
                ` : ''}
                <!-- University Name -->
                <div class="mb-3 pb-3 border-b border-gray-200 dark:border-gray-700">
                    <h3 class="text-xl font-bold text-gray-900 dark:text-white mb-1">
                        <a href="/universities/${result.university_slug}/" class="hover:text-primary transition-colors">
                            ${result.university_name}
                        </a>
                    </h3>
                    <p class="text-sm text-gray-600 dark:text-gray-400">${result.location}</p>
                </div>
                
                <!-- Course Title -->
                <div class="mb-3 pb-3 border-b border-gray-200 dark:border-gray-700">
                    <p class="text-base font-semibold text-gray-800 dark:text-gray-200">${result.course_title}</p>
                    <p class="text-sm text-gray-600 dark:text-gray-400 mt-1">${result.duration} • Starts ${result.start_date}</p>
                </div>
                
                <!-- Tuition Fee -->
                <div class="mb-3 pb-3 border-b border-gray-200 dark:border-gray-700">
                    <p class="text-sm text-gray-600 dark:text-gray-400 mb-1">Tuition Fee</p>
                    <p class="text-lg font-bold text-gray-900 dark:text-white">${tuitionOriginal}</p>
                    ${userCurrency !== currency ? `<p class="text-sm text-gray-500 dark:text-gray-400">≈ ${tuitionConverted}</p>` : ''}
                </div>
                
                <!-- Tuition Deposit -->
                <div class="mb-3 pb-3 border-b border-gray-200 dark:border-gray-700">
                    <p class="text-sm text-gray-600 dark:text-gray-400 mb-1">Tuition Deposit</p>
                    <p class="text-lg font-bold text-gray-900 dark:text-white">${depositOriginal}</p>
                    ${depositConverted && userCurrency !== currency ? `<p class="text-sm text-gray-500 dark:text-gray-400">≈ ${depositConverted}</p>` : ''}
                </div>
                
                <!-- Scholarship Info -->
                ${result.scholarship > 0 ? `
                <div class="mb-3 pb-3 border-b border-gray-200 dark:border-gray-700">
                    <p class="text-sm font-semibold text-green-800 dark:text-green-400 mb-1">Scholarship Available</p>
                    <p class="text-sm text-green-700 dark:text-green-300">
                        Save ${savingsAmount} (${savingsPercent}% off)
                    </p>
                </div>
                ` : ''}
                
                <!-- Net Tuition Fee (no border) -->
                <div class="mb-4">
                    <p class="text-sm text-gray-600 dark:text-gray-400 mb-1">Net Tuition Fee</p>
                    <p class="text-lg font-bold text-gray-900 dark:text-white">${netTuitionOriginal}</p>
                    ${userCurrency !== currency ? `<p class="text-sm text-gray-500 dark:text-gray-400">≈ ${netTuitionConverted}</p>` : ''}
                </div>
                
                <!-- Action Button -->
                <a href="${courseDetailUrl}" 
                   class="block w-full text-center px-4 py-3 rounded-lg font-semibold transition-all duration-200 border-2"
                   style="border-color: #102C46; color: #102C46;"
                   onmouseover="this.style.backgroundColor='#102C46'; this.style.color='white';"
                   onmouseout="this.style.backgroundColor='transparent'; this.style.color='#102C46';">
                    View Course Details
                </a>
            </div>
        </div>
    `;
}

// Convert currency
function convertCurrency(amount, fromCurrency, toCurrency) {
    if (fromCurrency === toCurrency) return amount;
    
    // Convert to GBP first, then to target currency
    const gbpAmount = amount / (exchangeRates[fromCurrency] || 1);
    return gbpAmount * (exchangeRates[toCurrency] || 1);
}

// Format currency
function formatCurrency(amount, currency) {
    const symbol = currencySymbols[currency] || currency;
    return `${symbol}${Math.round(amount).toLocaleString()}`;
}

// Sort results
function sortResults(sortBy) {
    if (sortBy === 'lowest') {
        allResults.sort((a, b) => a.net_tuition - b.net_tuition);
    } else if (sortBy === 'highest') {
        allResults.sort((a, b) => b.net_tuition - a.net_tuition);
    }
    
    displayResults(allResults);
}

// Filter results by level
function filterResultsByLevel(level) {
    console.log('=== FILTER DEBUG ===');
    console.log('Filter level selected:', level);
    console.log('allResultsUnfiltered length:', allResultsUnfiltered.length);
    
    if (!level) {
        // If no level selected, show all results
        allResults = [...allResultsUnfiltered];
        console.log('No filter - showing all results');
    } else {
        // Filter results by the selected level
        console.log('Filtering for level:', level);
        
        // Sample the first few courses to see what we're working with
        console.log('Sample course_level values from first 3 courses:');
        allResultsUnfiltered.slice(0, 3).forEach((r, i) => {
            console.log(`  Course ${i+1}: "${r.course_level}"`);
        });
        
        allResults = allResultsUnfiltered.filter(result => {
            return result.course_level && result.course_level.toUpperCase() === level.toUpperCase();
        });
        
        console.log('Filtered results count:', allResults.length);
    }
    
    // Check if filter returned no results
    if (allResults.length === 0) {
        console.log('⚠️ No results after filtering - showing no results message');
        showNoResults();
        return;
    }
    
    console.log('✅ Displaying', allResults.length, 'filtered results');
    
    // Sort by lowest fee (default)
    allResults.sort((a, b) => a.net_tuition - b.net_tuition);
    displayResults(allResults);
}
