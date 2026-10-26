// Compare Rankings functionality

// Load rankings data from API
async function loadRankings(country) {
    try {
        const countryCode = country === 'uk' ? 'UK' : 'IE';
        const response = await fetch(`/api/rankings/?country=${countryCode}`);
        const data = await response.json();
        
        if (data.error) {
            console.error('Error loading rankings:', data.error);
            return [];
        }
        
        console.log(`Loaded ${data.count} rankings for ${country}`);
        return data.rankings;
    } catch (error) {
        console.error('Error loading rankings:', error);
        return [];
    }
}

// Convert number to ordinal (1st, 2nd, 3rd, etc.)
function getOrdinal(n) {
    const s = ["th", "st", "nd", "rd"];
    const v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// Update country description
function updateCountryDescription(country) {
    const descSection = document.getElementById('country-description');
    const title = descSection.querySelector('h2');
    const text = descSection.querySelector('p');
    
    if (country === 'uk') {
        title.textContent = 'UK University Rankings';
        text.innerHTML = 'This is a ranking of the best universities in the UK. Here, you will see what the top universities in England, Scotland, Wales, and Northern Ireland are as ranked by world\'s most credible sources. This ranking features universities from vibrant cities across the nation including Manchester, London, Liverpool, Norwich, Birmingham, Coventry, Edinburgh, Glasgow, Cardiff, and many more.<br><br>The UK is home to some of the oldest and most prestigious universities in the world and is consistently ranked as one of the best countries to study in globally. Our UK universities ranking takes into account the university\'s overall academic excellence, subject-specific strengths, research output, teaching quality, graduate employability prospects, and student satisfaction scores.';
    } else {
        title.textContent = 'Ireland University Rankings';
        text.innerHTML = 'This is a ranking of the best universities in Ireland. Here, you will see what the top universities in the Republic of Ireland are as ranked by world\'s most credible sources. This ranking features universities from major Irish cities including Dublin, Cork, Galway, Limerick, Maynooth, and more.<br><br>Ireland has established itself as a leading destination for international students, offering world-class education in a welcoming, English-speaking environment. Irish universities are renowned for their research excellence, innovative teaching methods, and strong industry connections. Our Ireland universities ranking evaluates institutions based on their overall academic performance, subject-specific rankings, research quality, graduate career prospects, and student satisfaction.';
    }
}

// Handle form submission
document.addEventListener('DOMContentLoaded', async function() {
    // Load UK rankings by default on page load
    await compareRankings('uk');
    
    // Handle country change
    const countrySelect = document.getElementById('ranking-country');
    countrySelect.addEventListener('change', async function() {
        const country = this.value;
        if (country) {
            await compareRankings(country);
        }
    });
});

// Compare rankings
async function compareRankings(country) {
    const countryName = country === 'uk' ? 'United Kingdom' : 'Ireland';
    
    // Load rankings for the selected country
    const rankings = await loadRankings(country);
    
    if (rankings.length === 0) {
        alert('No rankings found for the selected country');
        return;
    }
    
    // Sort by overall score (descending)
    rankings.sort((a, b) => parseFloat(b.overall) - parseFloat(a.overall));
    
    // Update country description
    updateCountryDescription(country);
    
    // Display results
    displayRankingsTable(rankings);
    
    // Load undergraduate universities for carousel
    await loadUndergraduateUniversities(country);
}

// Display rankings table
function displayRankingsTable(rankings) {
    const tableBody = document.getElementById('rankings-table-body');
    
    tableBody.innerHTML = '';
    
    rankings.forEach((ranking, index) => {
        const overall = parseFloat(ranking.overall) || 0;
        const artsHumanities = parseFloat(ranking.arts_humanities) || 0;
        const lifeSciences = parseFloat(ranking.life_sciences_medicine) || 0;
        const engineering = parseFloat(ranking.engineering_technology) || 0;
        const naturalScience = parseFloat(ranking.natural_sciences) || 0;
        const socialSciences = parseFloat(ranking.social_sciences_management) || 0;
        const staffRatio = parseFloat(ranking.staff_student_ratio) || 0;
        const intlStudents = parseFloat(ranking.international_students_ratio) || 0;
        const graduateProspects = parseFloat(ranking.graduate_prospects) || 0;
        const studentSatisfaction = parseFloat(ranking.student_satisfaction) || 0;
        
        // Format subject rankings (lower is better, 0 means not ranked)
        const formatSubjectRank = (rank) => {
            if (!rank || rank === 0) return '<span class="text-gray-400">N/A</span>';
            return `<span class="font-semibold">${rank}</span>`;
        };
        
        // Desktop table row
        const row = document.createElement('tr');
        row.className = index % 2 === 0 ? 'bg-gray-50 dark:bg-secondary/50' : 'bg-white dark:bg-secondary/30';
        row.innerHTML = `
            <td class="px-3 py-3 text-xs font-semibold text-gray-600 dark:text-gray-400">${getOrdinal(index + 1)}</td>
            <td class="px-3 py-3">
                <div class="font-semibold text-sm">${ranking.university_name || 'N/A'}</div>
                <a href="/universities/${ranking.university_slug || ''}/courses/?level=UG" class="text-xs underline hover:opacity-80 transition-colors" style="color: #4A90E2;">
                    View All Courses
                </a>
            </td>
            <td class="px-4 py-3 text-center">
                <span class="inline-block px-2 py-1 font-bold text-black dark:text-white text-sm">
                    ${overall.toFixed(1)}
                </span>
            </td>
            <td class="px-4 py-3 text-center text-xs">${formatSubjectRank(artsHumanities)}</td>
            <td class="px-4 py-3 text-center text-xs">${formatSubjectRank(lifeSciences)}</td>
            <td class="px-4 py-3 text-center text-xs">${formatSubjectRank(engineering)}</td>
            <td class="px-4 py-3 text-center text-xs">${formatSubjectRank(naturalScience)}</td>
            <td class="px-4 py-3 text-center text-xs">${formatSubjectRank(socialSciences)}</td>
            <td class="px-4 py-3 text-center">
                <div class="flex items-center justify-center">
                    <div class="w-16 bg-gray-200 dark:bg-gray-700 rounded-full h-1.5 mr-1">
                        <div class="bg-blue-500 h-1.5 rounded-full" style="width: ${Math.min(100, intlStudents)}%"></div>
                    </div>
                    <span class="text-xs font-semibold">${intlStudents.toFixed(1)}%</span>
                </div>
            </td>
            <td class="px-4 py-3 text-center">
                <div class="flex items-center justify-center">
                    <div class="w-16 bg-gray-200 dark:bg-gray-700 rounded-full h-1.5 mr-1">
                        <div class="bg-green-500 h-1.5 rounded-full" style="width: ${graduateProspects}%"></div>
                    </div>
                    <span class="text-xs font-semibold">${graduateProspects.toFixed(1)}%</span>
                </div>
            </td>
            <td class="px-4 py-3 text-center">
                <div class="flex items-center justify-center">
                    <div class="w-16 bg-gray-200 dark:bg-gray-700 rounded-full h-1.5 mr-1">
                        <div class="bg-yellow-500 h-1.5 rounded-full" style="width: ${studentSatisfaction}%"></div>
                    </div>
                    <span class="text-xs font-semibold">${studentSatisfaction.toFixed(1)}%</span>
                </div>
            </td>
        `;
        tableBody.appendChild(row);
    });
}

// Load undergraduate universities for carousel
let currentCarouselIndex = 0;
let ugUniversitiesData = [];

async function loadUndergraduateUniversities(country) {
    try {
        // Reset carousel index when switching countries
        currentCarouselIndex = 0;
        
        const countryCode = country === 'uk' ? 'UK' : 'IE';
        const response = await fetch(`/api/universities/?country=${countryCode}`);
        const data = await response.json();
        
        if (data.error) {
            console.error('Error loading universities:', data.error);
            return;
        }
        
        ugUniversitiesData = data.universities || [];
        
        // Update count
        document.getElementById('ug-universities-count').textContent = `${ugUniversitiesData.length} universities`;
        
        // Display carousel
        displayUGCarousel();
    } catch (error) {
        console.error('Error loading undergraduate universities:', error);
    }
}

function displayUGCarousel() {
    const carousel = document.getElementById('ug-universities-carousel');
    carousel.innerHTML = '';
    
    ugUniversitiesData.forEach((university, index) => {
        const card = document.createElement('div');
        card.className = 'flex-none w-full md:w-[calc(25%-18px)] bg-white/10 backdrop-blur-md border border-white/20 rounded-lg overflow-hidden shadow-lg hover:shadow-xl transition-all';
        card.style.display = index < 4 ? 'block' : 'none';
        
        card.innerHTML = `
            <div class="relative">
                <img src="${university.banner || '/static/images/mine/about-us.jpg'}" alt="${university.name}" class="w-full h-48 object-cover" onerror="this.src='/static/images/mine/about-us.jpg'">
                <button class="absolute top-2 right-2 bg-white/90 dark:bg-secondary/90 p-2 rounded-full hover:bg-white dark:hover:bg-secondary transition-all favorite-btn" 
                        data-university-id="${university.university_id}"
                        title="Add to favorites">
                    <svg class="w-5 h-5 text-gray-400 hover:text-red-500 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z"></path>
                    </svg>
                </button>
                <span class="absolute top-2 left-2 bg-blue-500 text-white px-3 py-1 rounded-full text-xs font-semibold">
                    ${(university.ug_courses_count || 0) + (university.pg_courses_count || 0)} Courses
                </span>
            </div>
            <div class="p-5 flex flex-col h-[140px]">
                <h3 class="text-base font-bold line-clamp-2">${university.name}</h3>
                <a href="${university.website && university.website.includes('http') ? university.website : 'https://' + (university.website || '')}" target="_blank" class="block text-white rounded-lg text-center py-2 px-4 text-sm font-medium transition-all mt-auto" style="background-color: #102C46;" onmouseover="this.style.backgroundColor='#0a1f30'" onmouseout="this.style.backgroundColor='#102C46'">
                    Visit Website
                </a>
            </div>
        `;
        
        carousel.appendChild(card);
    });
    
    // Set up carousel navigation
    setupCarouselNavigation();
}

function setupCarouselNavigation() {
    const prevBtn = document.getElementById('ug-carousel-prev');
    const nextBtn = document.getElementById('ug-carousel-next');
    const carousel = document.getElementById('ug-universities-carousel');
    const container = document.getElementById('ug-carousel-container');
    const cards = carousel.children;
    
    // Desktop navigation buttons
    prevBtn.onclick = () => {
        if (currentCarouselIndex > 0) {
            currentCarouselIndex = Math.max(0, currentCarouselIndex - 4);
            updateCarouselDisplay(cards);
        }
    };
    
    nextBtn.onclick = () => {
        if (currentCarouselIndex < ugUniversitiesData.length - 4) {
            currentCarouselIndex = Math.min(ugUniversitiesData.length - 4, currentCarouselIndex + 4);
            updateCarouselDisplay(cards);
        }
    };
    
    // Mobile touch swipe functionality
    let touchStartX = 0;
    let touchEndX = 0;
    
    container.addEventListener('touchstart', (e) => {
        touchStartX = e.changedTouches[0].screenX;
    }, false);
    
    container.addEventListener('touchend', (e) => {
        touchEndX = e.changedTouches[0].screenX;
        handleSwipe();
    }, false);
    
    function handleSwipe() {
        const swipeThreshold = 50;
        const diff = touchStartX - touchEndX;
        
        if (Math.abs(diff) > swipeThreshold) {
            if (window.innerWidth < 768) { // Mobile only
                if (diff > 0 && currentCarouselIndex < ugUniversitiesData.length - 1) {
                    // Swipe left - next card
                    currentCarouselIndex++;
                    updateCarouselDisplay(cards);
                } else if (diff < 0 && currentCarouselIndex > 0) {
                    // Swipe right - previous card
                    currentCarouselIndex--;
                    updateCarouselDisplay(cards);
                }
            }
        }
    }
}

function updateCarouselDisplay(cards) {
    const isMobile = window.innerWidth < 768;
    const cardsToShow = isMobile ? 1 : 4;
    
    Array.from(cards).forEach((card, index) => {
        card.style.display = (index >= currentCarouselIndex && index < currentCarouselIndex + cardsToShow) ? 'block' : 'none';
    });
}
