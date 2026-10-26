// Find Course Functionality with Autocomplete
document.addEventListener('DOMContentLoaded', function() {
    console.log('Find course script loaded');
    const searchForm = document.getElementById('course-search-form');
    const subjectInput = document.getElementById('course-subject');
    const suggestionsContainer = document.getElementById('find-course-suggestions');
    console.log('Form element:', searchForm);
    
    let debounceTimer;
    
    // Fetch subject suggestions from API
    async function fetchSubjects(query) {
        if (!query || query.length < 2) {
            suggestionsContainer.classList.add('hidden');
            return;
        }
        
        try {
            const response = await fetch(`/api/subjects/?q=${encodeURIComponent(query)}`);
            const data = await response.json();
            displaySuggestions(data.subjects);
        } catch (error) {
            console.error('Error fetching subjects:', error);
        }
    }
    
    // Display autocomplete suggestions
    function displaySuggestions(subjects) {
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
        
        // Add click event listeners to suggestions
        document.querySelectorAll('.suggestion-item').forEach(item => {
            item.addEventListener('click', function() {
                const name = this.dataset.name;
                subjectInput.value = name;
                suggestionsContainer.classList.add('hidden');
            });
        });
    }
    
    // Input event with debounce for autocomplete
    if (subjectInput) {
        subjectInput.addEventListener('input', function() {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                fetchSubjects(this.value.trim());
            }, 300);
        });
    }
    
    // Hide suggestions when clicking outside
    document.addEventListener('click', function(e) {
        if (subjectInput && !subjectInput.contains(e.target) && !suggestionsContainer.contains(e.target)) {
            suggestionsContainer.classList.add('hidden');
        }
    });
    
    // Form submission
    if (searchForm) {
        searchForm.addEventListener('submit', function(e) {
            e.preventDefault();
            console.log('Form submitted');
            
            // Get form values
            const level = document.getElementById('level-of-study').value;
            const country = document.getElementById('destination-country').value;
            const subject = document.getElementById('course-subject').value.trim();
            
            console.log('Form values:', { level, country, subject });
            
            // Validate form
            if (!level || !country || !subject) {
                alert('Please fill in all required fields');
                return;
            }
            
            // Hide suggestions
            suggestionsContainer.classList.add('hidden');
            
            // Redirect to course search results page with parameters
            const redirectUrl = `/course-search/?subject=${encodeURIComponent(subject)}&level=${level}&country=${country}`;
            console.log('Redirecting to:', redirectUrl);
            window.location.href = redirectUrl;
        });
    } else {
        console.error('Form not found!');
    }
});
