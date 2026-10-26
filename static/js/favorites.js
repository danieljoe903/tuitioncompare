// Favorites Page Management
document.addEventListener('DOMContentLoaded', function() {
    const favoritesGrid = document.getElementById('favorites-grid');
    const emptyState = document.getElementById('empty-state');
    const favoritesCount = document.getElementById('favorites-count');
    const clearAllBtn = document.getElementById('clear-all-favorites');
    
    // Get all favorites
    function getAllFavorites() {
        const universities = JSON.parse(localStorage.getItem('universityFavorites') || '[]');
        return { universities };
    }
    
    // Create university card
    function createUniversityCard(university) {
        const imageUrl = university.banner || '/static/images/mine/about-us.jpg';
        const location = university.city && university.country 
            ? `${university.city}, ${university.country}` 
            : university.city || university.country || 'Location not available';
        
        const cardHTML = `
            <div class="bg-white dark:bg-secondary rounded-lg shadow-round-box border border-gray-200 dark:border-gray-700 hover:shadow-xl transition-all p-6" data-type="university" data-id="${university.university_id}">
                <div class="flex items-center gap-3 mb-4">
                    <img src="${imageUrl}" alt="${university.name}" class="w-16 h-16 object-cover rounded-lg border border-gray-200 dark:border-gray-600 flex-shrink-0">
                    <div class="flex-1 min-w-0">
                        <a href="/universities/${university.slug}/" class="text-lg font-bold text-gray-900 dark:text-white hover:text-primary transition-colors block">${university.name}</a>
                        <p class="text-gray-600 dark:text-gray-400 text-sm">${location}</p>
                    </div>
                </div>
                <div class="flex flex-wrap gap-3">
                    <a href="/universities/${university.slug}/" class="px-6 py-2.5 text-white rounded-lg text-sm font-medium transition-all hover:opacity-90" style="background-color: #102C46;">
                        View University
                    </a>
                    <button class="remove-favorite px-6 py-2.5 bg-red-500 hover:bg-red-600 text-white rounded-lg text-sm font-medium transition-all" data-type="university" data-id="${university.university_id}">
                        Remove
                    </button>
                </div>
            </div>
        `;
        
        return cardHTML;
    }
    
    // Create course card
    function createCourseCard(course) {
        return `
            <div class="bg-white dark:bg-secondary rounded-lg shadow-round-box p-6 border border-gray-200 dark:border-gray-700 hover:shadow-xl transition-all" data-type="course" data-id="${course.course_id}">
                <div class="flex justify-between items-start mb-3">
                    <div class="flex-1">
                        <h3 class="text-lg font-bold text-gray-900 dark:text-white mb-2">${course.course_title}</h3>
                        <p class="text-sm text-gray-600 dark:text-gray-400 mb-1">
                            <span class="font-medium">${course.level === 'UG' ? 'Undergraduate' : 'Postgraduate'}</span>
                        </p>
                        <p class="text-sm text-gray-600 dark:text-gray-400 mb-1">${course.location}</p>
                        <p class="text-sm text-gray-500 dark:text-gray-500">${course.university_name}</p>
                    </div>
                </div>
                <div class="flex flex-wrap gap-3 mt-4">
                    <a href="/universities/${course.university_slug}/courses/?level=${course.level}" class="px-6 py-2.5 text-white rounded-lg text-sm font-medium transition-all" style="background-color: #3B82F6;" onmouseover="this.style.backgroundColor='#2563eb'" onmouseout="this.style.backgroundColor='#3B82F6'">
                        View Course
                    </a>
                    <a href="${course.university_website.startsWith('http') ? course.university_website : 'https://' + course.university_website}" target="_blank" class="px-6 py-2.5 text-white rounded-lg text-sm font-medium transition-all hover:opacity-90" style="background-color: #102C46;">
                        Visit Website
                    </a>
                    <button class="remove-favorite px-6 py-2.5 bg-red-500 hover:bg-red-600 text-white rounded-lg text-sm font-medium transition-all" data-type="course" data-id="${course.course_id}">
                        Remove
                    </button>
                </div>
            </div>
        `;
    }
    
    // Render all favorites
    function renderFavorites() {
        const { universities } = getAllFavorites();
        const totalCount = universities.length;
        
        console.log('=== RENDER FAVORITES DEBUG ===');
        console.log('Total universities:', totalCount);
        console.log('Universities data:', universities);
        
        if (totalCount === 0) {
            favoritesGrid.innerHTML = '';
            favoritesGrid.style.display = 'none';
            emptyState.style.display = 'block';
            emptyState.classList.remove('hidden');
            favoritesCount.textContent = '0 favorites';
            if (clearAllBtn) clearAllBtn.style.display = 'none';
        } else {
            emptyState.style.display = 'none';
            emptyState.classList.add('hidden');
            favoritesGrid.style.display = 'block';
            if (clearAllBtn) clearAllBtn.style.display = 'block';
            favoritesCount.textContent = `${totalCount} ${totalCount === 1 ? 'university' : 'universities'}`;
            
            console.log('Building HTML for', universities.length, 'universities');
            const cardsHTML = universities.map(uni => {
                console.log('Mapping university:', uni.name);
                return createUniversityCard(uni);
            }).join('');
            
            console.log('Cards HTML length:', cardsHTML.length);
            console.log('Setting innerHTML...');
            favoritesGrid.innerHTML = cardsHTML;
            console.log('innerHTML set. favoritesGrid.children.length:', favoritesGrid.children.length);
            
            // Add event listeners to remove buttons
            const removeButtons = document.querySelectorAll('.remove-favorite');
            console.log('Found remove buttons:', removeButtons.length);
            removeButtons.forEach(button => {
                button.addEventListener('click', function() {
                    const type = this.dataset.type;
                    const id = this.dataset.id;
                    removeFavorite(type, id);
                });
            });
        }
    }
    
    // Remove single favorite
    function removeFavorite(type, id) {
        if (type === 'university') {
            let favorites = JSON.parse(localStorage.getItem('universityFavorites') || '[]');
            favorites = favorites.filter(fav => fav.university_id !== id);
            localStorage.setItem('universityFavorites', JSON.stringify(favorites));
        }
        
        renderFavorites();
        showToast('Removed from favorites');
    }
    
    // Clear all favorites
    if (clearAllBtn) {
        clearAllBtn.addEventListener('click', function() {
            if (confirm('Are you sure you want to remove all favorites?')) {
                localStorage.setItem('universityFavorites', '[]');
                renderFavorites();
                showToast('All favorites cleared');
            }
        });
    }
    
    // Show toast
    function showToast(message) {
        const toast = document.createElement('div');
        toast.className = 'fixed bottom-4 right-4 bg-green-500 text-white px-6 py-3 rounded-lg shadow-lg z-50';
        toast.textContent = message;
        document.body.appendChild(toast);
        
        setTimeout(() => {
            toast.remove();
        }, 3000);
    }
    
    // Initial render
    renderFavorites();
});
