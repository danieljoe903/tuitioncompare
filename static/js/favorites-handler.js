// Global Favorites Handler for University Cards
// Unified storage key
const FAVORITES_KEY = 'universityFavorites';

function initializeFavoriteButtons() {
    document.querySelectorAll('.favorite-btn').forEach(button => {
        // Remove existing click listeners by cloning
        const newButton = button.cloneNode(true);
        button.parentNode.replaceChild(newButton, button);
        
        newButton.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            
            const universityId = this.getAttribute('data-university-id');
            if (universityId) {
                toggleFavorite(universityId, this);
            }
        });
    });
    
    // Update button states based on current favorites
    updateAllFavoriteButtons();
}

function toggleFavorite(universityId, button) {
    if (!button) {
        button = document.querySelector(`.favorite-btn[data-university-id="${universityId}"]`);
    }
    if (!button) return;
    
    // Get university data from the card
    // Try multiple approaches to find the parent card
    let card = button.closest('.bg-white, .bg-white\\/10, [class*="bg-white"]');
    
    // If button is deeply nested (like in event-ticket), go up more levels
    if (!card) {
        let parent = button.parentElement;
        while (parent && !card) {
            if (parent.classList.contains('backdrop-blur-md') || 
                parent.classList.contains('rounded-lg') ||
                parent.querySelector('h3, h2')) {
                card = parent;
                break;
            }
            parent = parent.parentElement;
        }
    }
    
    if (!card) {
        console.error('Could not find card element');
        return;
    }
    
    const universityData = extractUniversityData(card, universityId);
    
    let favorites = JSON.parse(localStorage.getItem(FAVORITES_KEY) || '[]');
    const index = favorites.findIndex(fav => fav.university_id === universityId);
    
    if (index > -1) {
        // Remove from favorites
        favorites.splice(index, 1);
        localStorage.setItem(FAVORITES_KEY, JSON.stringify(favorites));
        updateFavoriteButton(button, false);
        showToastMessage(`${universityData.name} removed from favorites`);
    } else {
        // Add to favorites
        favorites.push(universityData);
        localStorage.setItem(FAVORITES_KEY, JSON.stringify(favorites));
        updateFavoriteButton(button, true);
        showToastMessage(`${universityData.name} added to favorites`);
    }
}

function extractUniversityData(card, universityId) {
    const data = {
        university_id: universityId,
        type: 'university'
    };
    
    // Try to extract name - search in card and parent
    let nameEl = card.querySelector('h3, h2');
    if (!nameEl) {
        // If button is in a nested div (like event-ticket), search the whole card
        const parentCard = card.closest('div[class*="bg-white"]') || card;
        nameEl = parentCard.querySelector('h3, h2');
    }
    data.name = nameEl ? nameEl.textContent.trim() : 'University';
    
    // Try to extract location - look for the sibling paragraph after h3
    let locationEl = null;
    if (nameEl) {
        // Get the next sibling that's a paragraph
        let sibling = nameEl.nextElementSibling;
        while (sibling) {
            if (sibling.tagName === 'P' && sibling.textContent.includes(',')) {
                locationEl = sibling;
                break;
            }
            sibling = sibling.nextElementSibling;
        }
    }
    
    // If still not found, search in the parent card
    if (!locationEl) {
        const parentCard = card.closest('div[class*="bg-white"]') || card;
        const paragraphs = parentCard.querySelectorAll('p.text-gray-600, p[class*="text-gray"]');
        for (const p of paragraphs) {
            const text = p.textContent.trim();
            // Check if it contains location info (has a comma and city/country keywords)
            if (text.includes(',') && (text.includes('Ireland') || text.includes('UK') || text.includes('United Kingdom') || text.match(/[A-Z][a-z]+,\s*[A-Z]/))) {
                locationEl = p;
                break;
            }
        }
    }
    
    if (locationEl) {
        const location = locationEl.textContent.trim();
        const parts = location.split(',').map(p => p.trim());
        data.city = parts[0] || '';
        // Get the actual country text, not a default
        data.country = parts[1] || '';
    } else {
        // Default values if location not found
        data.city = '';
        data.country = '';
    }
    
    // Try to extract slug from link
    let link = card.querySelector('a[href*="/universities/"]');
    if (!link) {
        const parentCard = card.closest('div[class*="bg-white"]') || card;
        link = parentCard.querySelector('a[href*="/universities/"]');
    }
    if (link) {
        const href = link.getAttribute('href');
        const slugMatch = href.match(/\/universities\/([^\/]+)/);
        data.slug = slugMatch ? slugMatch[1] : universityId;
    } else {
        data.slug = universityId;
    }
    
    // Try to extract banner image - look for the main university image
    let img = card.querySelector('img');
    if (!img) {
        const parentCard = card.closest('div[class*="bg-white"]') || card;
        img = parentCard.querySelector('img');
    }
    if (img) {
        data.banner = img.src;
    }
    
    return data;
}

function updateFavoriteButton(button, isFavorited) {
    const svg = button.querySelector('svg');
    if (!svg) return;
    
    if (isFavorited) {
        svg.setAttribute('fill', 'currentColor');
        svg.classList.remove('text-gray-400');
        svg.classList.add('text-red-500');
        button.title = 'Remove from favorites';
    } else {
        svg.setAttribute('fill', 'none');
        svg.classList.remove('text-red-500');
        svg.classList.add('text-gray-400');
        button.title = 'Add to favorites';
    }
}

function updateAllFavoriteButtons() {
    const favorites = JSON.parse(localStorage.getItem(FAVORITES_KEY) || '[]');
    const favoriteIds = favorites.map(fav => fav.university_id);
    
    document.querySelectorAll('.favorite-btn').forEach(button => {
        const universityId = button.getAttribute('data-university-id');
        if (universityId && favoriteIds.includes(universityId)) {
            updateFavoriteButton(button, true);
        }
    });
}

function showToastMessage(message) {
    // Check if toast exists
    let toast = document.getElementById('favorite-toast');
    
    if (!toast) {
        // Create toast if it doesn't exist
        toast = document.createElement('div');
        toast.id = 'favorite-toast';
        toast.className = 'fixed top-24 right-4 bg-green-500 text-white px-6 py-3 rounded-lg shadow-lg transition-all duration-300 z-50';
        toast.style.transform = 'translateX(400px)';
        document.body.appendChild(toast);
    }
    
    toast.textContent = message;
    toast.style.transform = 'translateX(0)';
    
    setTimeout(() => {
        toast.style.transform = 'translateX(400px)';
    }, 3000);
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', initializeFavoriteButtons);
