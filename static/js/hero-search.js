// Hero Section Search Functionality with Autocomplete
document.addEventListener('DOMContentLoaded', function() {
    const searchInput = document.getElementById('hero-search-input');
    const levelSelect = document.getElementById('hero-level-select');
    const searchBtn = document.getElementById('hero-search-btn');
    const suggestionsContainer = document.getElementById('hero-suggestions');
    
    console.log('Hero search initialized:', {
        searchInput: !!searchInput,
        levelSelect: !!levelSelect,
        searchBtn: !!searchBtn,
        suggestionsContainer: !!suggestionsContainer
    });
    
    if (!searchInput || !levelSelect || !searchBtn || !suggestionsContainer) {
        console.error('One or more hero search elements not found');
        return;
    }
    
    let debounceTimer;
    let selectedSubject = '';
    
    // Fetch subject suggestions from API
    async function fetchSubjects(query) {
        console.log('Fetching subjects for query:', query);
        if (!query || query.length < 2) {
            suggestionsContainer.classList.add('hidden');
            return;
        }
        
        try {
            const response = await fetch(`/api/subjects/?q=${encodeURIComponent(query)}`);
            const data = await response.json();
            console.log('Subjects received:', data);
            displaySuggestions(data.subjects);
        } catch (error) {
            console.error('Error fetching subjects:', error);
        }
    }
    
    // Display autocomplete suggestions
    function displaySuggestions(subjects) {
        console.log('Displaying suggestions:', subjects.length);
        if (subjects.length === 0) {
            suggestionsContainer.classList.add('hidden');
            return;
        }
        
        const html = subjects.map(subject => `
            <div class="suggestion-item px-4 py-3 hover:bg-gray-100 dark:hover:bg-darklight cursor-pointer border-b border-gray-200 dark:border-gray-600 last:border-b-0" data-name="${subject.name}">
                <p class="text-sm font-medium text-gray-900 dark:text-white">${subject.name}</p>
            </div>
        `).join('');
        
        suggestionsContainer.innerHTML = html;
        suggestionsContainer.classList.remove('hidden');
        console.log('Suggestions displayed, hidden class removed');
        
        // Add click event listeners to suggestions
        document.querySelectorAll('.suggestion-item').forEach(item => {
            item.addEventListener('click', function() {
                const name = this.dataset.name;
                searchInput.value = name;
                selectedSubject = name;
                suggestionsContainer.classList.add('hidden');
            });
        });
    }
    
    // Input event with debounce
    searchInput.addEventListener('input', function() {
        console.log('Input event, value:', this.value);
        clearTimeout(debounceTimer);
        selectedSubject = '';
        debounceTimer = setTimeout(() => {
            fetchSubjects(this.value.trim());
        }, 300);
    });
    
    // Hide suggestions when clicking outside
    document.addEventListener('click', function(e) {
        if (!searchInput.contains(e.target) && !suggestionsContainer.contains(e.target)) {
            suggestionsContainer.classList.add('hidden');
        }
    });
    
    // Search button click - redirect to course search page
    searchBtn.addEventListener('click', function() {
        const subject = searchInput.value.trim();
        const level = levelSelect.value === 'undergraduate' ? 'UG' : 'PG';
        
        if (!subject) {
            alert('Please enter a course or subject');
            return;
        }
        
        // Redirect to course search page with parameters
        window.location.href = `/course-search/?subject=${encodeURIComponent(subject)}&level=${level}`;
    });
    
    // Allow Enter key to trigger search
    searchInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            suggestionsContainer.classList.add('hidden');
            searchBtn.click();
        }
    });
});
