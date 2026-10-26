// University Detail Page Functionality
document.addEventListener('DOMContentLoaded', function() {
    loadUniversityDetails();
    
    // Initialize AOS animations
    if (typeof AOS !== 'undefined') {
        AOS.init({
            duration: 800,
            once: true
        });
    }
    
    // Smooth scroll for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(e) {
            const href = this.getAttribute('href');
            // Only handle internal anchors, not # or external URLs
            if (href && href.startsWith('#') && href.length > 1) {
                e.preventDefault();
                const target = document.querySelector(href);
                if (target) {
                    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            }
        });
    });
    
    // Toggle expand/collapse for long content sections
    const expandButtons = document.querySelectorAll('[data-expand]');
    expandButtons.forEach(button => {
        button.addEventListener('click', function() {
            const targetId = this.getAttribute('data-expand');
            const target = document.getElementById(targetId);
            
            if (target) {
                target.classList.toggle('max-h-96');
                target.classList.toggle('overflow-hidden');
                this.textContent = target.classList.contains('max-h-96') ? 'Show More' : 'Show Less';
            }
        });
    });
    
    // Tab switching for Tuition Fees and Scholarships
    const tuitionTab = document.getElementById('tuition-tab');
    const scholarshipsTab = document.getElementById('scholarships-tab');
    const tuitionContent = document.getElementById('tuition-content');
    const scholarshipsContent = document.getElementById('scholarships-content');
    
    if (tuitionTab && scholarshipsTab) {
        tuitionTab.addEventListener('click', function() {
            // Set active state
            tuitionTab.style.borderColor = '#102C46';
            tuitionTab.style.color = '#102C46';
            tuitionTab.classList.remove('border-transparent');
            scholarshipsTab.style.borderColor = 'transparent';
            scholarshipsTab.classList.add('border-transparent');
            scholarshipsTab.classList.remove('text-gray-900', 'dark:text-white');
            scholarshipsTab.classList.add('text-gray-600', 'dark:text-gray-400');
            
            // Show/hide content
            tuitionContent.classList.remove('hidden');
            scholarshipsContent.classList.add('hidden');
        });
        
        scholarshipsTab.addEventListener('click', function() {
            // Set active state
            scholarshipsTab.style.borderColor = '#102C46';
            scholarshipsTab.style.color = '#102C46';
            scholarshipsTab.classList.remove('border-transparent');
            tuitionTab.style.borderColor = 'transparent';
            tuitionTab.classList.add('border-transparent');
            tuitionTab.classList.remove('text-gray-900', 'dark:text-white');
            tuitionTab.classList.add('text-gray-600', 'dark:text-gray-400');
            
            // Show/hide content
            scholarshipsContent.classList.remove('hidden');
            tuitionContent.classList.add('hidden');
        });
    }
});

function loadUniversityDetails() {
    // Get slug from URL
    const pathParts = window.location.pathname.split('/').filter(part => part);
    const slug = pathParts[pathParts.length - 1]; // Get slug from URL like /universities/american-college-dublin/
    
    // Fetch university data from API
    fetch(`/api/universities/?slug=${slug}`)
        .then(response => response.json())
        .then(data => {
            if (data.universities && data.universities.length > 0) {
                const university = data.universities[0];
                displayUniversityBanner(university);
                displayUniversityInfo(university);
                loadScholarships(university.university_id);
            } else {
                console.error('University not found');
            }
        })
        .catch(error => {
            console.error('Error loading university details:', error);
        });
}

function displayUniversityBanner(university) {
    // Set images based on country
    setCountrySpecificImages(university.country);
    
    // Update university name
    const nameElement = document.getElementById('university-name');
    if (nameElement) {
        nameElement.textContent = university.name;
    }
    
    // Update location
    const locationElement = document.getElementById('university-location');
    if (locationElement) {
        const countryName = university.country === 'UK' ? 'United Kingdom' : 'Ireland';
        locationElement.textContent = `${university.city}, ${countryName}`;
    }
    
    // Update Visit Website button
    const visitWebsiteBtn = document.getElementById('visit-website-btn');
    if (visitWebsiteBtn && university.website) {
        let websiteUrl = university.website;
        if (!websiteUrl.startsWith('http://') && !websiteUrl.startsWith('https://')) {
            websiteUrl = 'https://' + websiteUrl;
        }
        visitWebsiteBtn.href = websiteUrl;
    }
    
    // Update tuition and scholarship website buttons
    const tuitionBtn = document.getElementById('tuition-website-btn');
    const scholarshipBtn = document.getElementById('scholarship-website-btn');
    if (university.website) {
        let websiteUrl = university.website;
        if (!websiteUrl.startsWith('http://') && !websiteUrl.startsWith('https://')) {
            websiteUrl = 'https://' + websiteUrl;
        }
        if (tuitionBtn) tuitionBtn.href = websiteUrl;
        if (scholarshipBtn) scholarshipBtn.href = websiteUrl;
    }
    
    // Update browse courses banner title
    const browseTitleElement = document.getElementById('browse-courses-title');
    if (browseTitleElement) {
        browseTitleElement.textContent = `Browse UG and PG at ${university.name}`;
    }
    
    // Update UG and PG course buttons
    const ugCoursesBtn = document.getElementById('ug-courses-btn');
    const pgCoursesBtn = document.getElementById('pg-courses-btn');
    if (ugCoursesBtn) {
        ugCoursesBtn.href = `/universities/${university.slug}/courses/?level=UG`;
    }
    if (pgCoursesBtn) {
        pgCoursesBtn.href = `/universities/${university.slug}/courses/?level=PG`;
    }
    
    // Update tuition fees text
    const tuitionTextElement = document.getElementById('tuition-text');
    if (tuitionTextElement) {
        tuitionTextElement.textContent = `Tuition fees for ${university.name} students may vary depending on your level, programme, home status and course.`;
    }
    
    // Update banner background image
    const bannerElement = document.getElementById('university-banner');
    if (bannerElement && university.banner) {
        bannerElement.style.backgroundImage = `linear-gradient(rgba(0, 0, 0, 0.65), rgba(0, 0, 0, 0.65)), url('${university.banner}')`;
    }
    
    // Update living costs
    const livingCostElement = document.getElementById('living-cost-amount');
    if (livingCostElement && university.estimated_living_cost_annual) {
        const currency = university.country === 'UK' ? '£' : '€';
        livingCostElement.textContent = `${currency}${parseFloat(university.estimated_living_cost_annual).toLocaleString()} / year`;
    } else if (livingCostElement) {
        livingCostElement.textContent = 'Contact university for details';
    }
    
    // Update admission requirements
    const admissionReqElement = document.getElementById('admission-requirements');
    if (admissionReqElement && university.admission_requirements) {
        admissionReqElement.textContent = university.admission_requirements;
    } else if (admissionReqElement) {
        admissionReqElement.textContent = 'Please visit the university website for detailed admission requirements.';
    }
    
    // Update acceptance rate in admissions section
    const admissionAcceptanceElement = document.getElementById('admission-acceptance-rate');
    if (admissionAcceptanceElement && university.acceptance_rate) {
        admissionAcceptanceElement.textContent = `${university.acceptance_rate}%`;
    }
    
    // Update graduation rate
    const graduationRateElement = document.getElementById('graduation-rate');
    if (graduationRateElement && university.graduation_rate) {
        graduationRateElement.textContent = `${university.graduation_rate}%`;
    }
    
    // Update global ranking from UniversityRanking world_ranking field
    const globalRankingElement = document.getElementById('global-ranking');
    const rankingBadge = document.getElementById('ranking-badge');
    if (university.world_ranking && university.world_ranking > 0) {
        if (globalRankingElement) {
            globalRankingElement.textContent = `#${university.world_ranking} Worldwide`;
        }
    } else {
        // Hide ranking badge if no ranking available
        if (rankingBadge) {
            rankingBadge.style.display = 'none';
        }
    }
    
    // Update banner background if logo exists (placeholder for now)
    // When you add banner images, you can update this:
    // const bannerElement = document.getElementById('university-banner');
    // if (bannerElement && university.banner) {  
    //     bannerElement.style.backgroundImage = `url('${university.banner}')`;  
    // }
}

function setCountrySpecificImages(country) {
    const isUK = country === 'UK';
    
    // Tuition Fees image
    const tuitionImage = document.getElementById('tuition-image');
    if (tuitionImage) {
        tuitionImage.src = isUK 
            ? 'https://cdn.universitycompare.com/content/images/UniPostCourseLectures--City-University-of-London.jpg'
            : 'https://cdn.universitycompare.com/content/images/UniPostTuitionFees--LSE-PG-Tuition-Fees.jpg';
    }
    
    // Scholarship image
    const scholarshipImage = document.getElementById('scholarship-image');
    if (scholarshipImage) {
        scholarshipImage.src = isUK
            ? 'https://cdn.universitycompare.com/content/images/UniPostStudentSupport--LSE-PG-Student-Support.jpg'
            : 'https://cdn.universitycompare.com/content/images/UniStudentUnion--LSE-Students-Union.jpg';
    }
    
    // Living Cost image
    const livingCostImage = document.getElementById('living-cost-image');
    if (livingCostImage) {
        livingCostImage.src = isUK
            ? 'https://cdn.universitycompare.com/content/images/Housing--7EVEMBNMXjE3MCC.jpg'
            : 'https://cdn.universitycompare.com/content/images/Housing--1Cek8rwC1rFvzuz.jpg';
    }
    
    // Admissions image
    const admissionsImage = document.getElementById('admissions-image');
    if (admissionsImage) {
        admissionsImage.src = isUK
            ? 'https://cdn.universitycompare.com/content/images/UniPostJobProspects--LSE-PG-Job-Prospects.jpg'
            : 'https://cdn.universitycompare.com/content/images/UniPostJobProspects--Birmingham-Jobs.jpg';
    }
}

function displayUniversityInfo(university) {
    const currency = university.country === 'UK' ? '£' : '€';
    const ugTuition = university.avg_ug_tuition ? `${currency}${university.avg_ug_tuition.toLocaleString()}` : 'N/A';
    const pgTuition = university.avg_pg_tuition ? `${currency}${university.avg_pg_tuition.toLocaleString()}` : 'N/A';
    const population = university.population ? university.population.toLocaleString() : 'N/A';
    const acceptanceRate = university.acceptance_rate ? `${university.acceptance_rate}%` : 'N/A';
    
    // Update overview
    const overviewElement = document.getElementById('university-overview');
    if (overviewElement && university.overview) {
        overviewElement.textContent = university.overview;
    }
    
    const infoHTML = `
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            <!-- Total Students Card -->
            <div class="bg-white/80 backdrop-blur-md dark:bg-dark_card/80 rounded-lg shadow-md p-6 border border-gray-200 dark:border-gray-700">
                <div class="flex items-start gap-4">
                    <div class="w-12 h-12 rounded-lg flex items-center justify-center flex-shrink-0" style="background-color: #102C46;">
                        <svg class="w-6 h-6" style="color: white;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"></path>
                        </svg>
                    </div>
                    <div class="flex-1">
                        <p class="text-2xl font-bold text-gray-900 dark:text-white">${population}</p>
                        <p class="text-sm text-gray-600 dark:text-gray-400 mt-1">Total Students</p>
                    </div>
                </div>
            </div>
            
            <!-- Acceptance Rate Card -->
            <div class="bg-white/80 backdrop-blur-md dark:bg-dark_card/80 rounded-lg shadow-md p-6 border border-gray-200 dark:border-gray-700">
                <div class="flex items-start gap-4">
                    <div class="w-12 h-12 rounded-lg flex items-center justify-center flex-shrink-0" style="background-color: #102C46;">
                        <svg class="w-6 h-6" style="color: white;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                    </div>
                    <div class="flex-1">
                        <p class="text-2xl font-bold text-gray-900 dark:text-white">${acceptanceRate}</p>
                        <p class="text-sm text-gray-600 dark:text-gray-400 mt-1">Acceptance Rate</p>
                    </div>
                </div>
            </div>
            
            <!-- Avg UG Tuition Card -->
            <div class="bg-white/80 backdrop-blur-md dark:bg-dark_card/80 rounded-lg shadow-md p-6 border border-gray-200 dark:border-gray-700">
                <div class="flex items-start gap-4">
                    <div class="w-12 h-12 rounded-lg flex items-center justify-center flex-shrink-0" style="background-color: #102C46;">
                        <svg class="w-6 h-6" style="color: white;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                    </div>
                    <div class="flex-1">
                        <p class="text-2xl font-bold text-gray-900 dark:text-white">${ugTuition}</p>
                        <p class="text-sm text-gray-600 dark:text-gray-400 mt-1">Avg UG Tuition</p>
                    </div>
                </div>
            </div>
            
            <!-- Avg PG Tuition Card -->
            <div class="bg-white/80 backdrop-blur-md dark:bg-dark_card/80 rounded-lg shadow-md p-6 border border-gray-200 dark:border-gray-700">
                <div class="flex items-start gap-4">
                    <div class="w-12 h-12 rounded-lg flex items-center justify-center flex-shrink-0" style="background-color: #102C46;">
                        <svg class="w-6 h-6" style="color: white;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                    </div>
                    <div class="flex-1">
                        <p class="text-2xl font-bold text-gray-900 dark:text-white">${pgTuition}</p>
                        <p class="text-sm text-gray-600 dark:text-gray-400 mt-1">Avg PG Tuition</p>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    const infoContainer = document.getElementById('university-info-cards');
    if (infoContainer) {
        infoContainer.innerHTML = infoHTML;
    }
}

function loadScholarships(university_id) {
    // Fetch scholarships for this university
    fetch(`/api/scholarships/?university_id=${university_id}`)
        .then(response => response.json())
        .then(data => {
            const scholarshipsList = document.getElementById('scholarships-list');
            if (scholarshipsList) {
                if (data.scholarships && data.scholarships.length > 0) {
                    // Create bullet point list
                    const listItems = data.scholarships.map(scholarship => {
                        return `<li class="mb-2">${scholarship.name} (${scholarship.level})</li>`;
                    }).join('');
                    scholarshipsList.innerHTML = listItems;
                } else {
                    scholarshipsList.innerHTML = '<li>No scholarships currently available. Please visit the university website for more information.</li>';
                }
            }
        })
        .catch(error => {
            console.error('Error loading scholarships:', error);
            const scholarshipsList = document.getElementById('scholarships-list');
            if (scholarshipsList) {
                scholarshipsList.innerHTML = '<li>Unable to load scholarships. Please visit the university website for more information.</li>';
            }
        });
}
