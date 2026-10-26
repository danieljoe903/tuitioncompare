// Initialize AOS (Animate On Scroll)
AOS.init({
    duration: 1000,
    once: true,
    offset: 100
});

// ======================================
// Theme Toggle (Dark/Light Mode)
// ======================================
const themeToggle = document.getElementById('theme-toggle');
const htmlElement = document.documentElement;

// Check for saved theme preference or default to 'light' mode
const currentTheme = localStorage.getItem('theme') || 'light';
htmlElement.classList.toggle('dark', currentTheme === 'dark');

if (themeToggle) {
    themeToggle.addEventListener('click', () => {
        htmlElement.classList.toggle('dark');
        const theme = htmlElement.classList.contains('dark') ? 'dark' : 'light';
        localStorage.setItem('theme', theme);
    });
}

// ======================================
// Sticky Header
// ======================================
const header = document.getElementById('header');
if (header) {
    window.addEventListener('scroll', () => {
        if (window.scrollY >= 80) {
            header.classList.add('shadow-lg');
        } else {
            header.classList.remove('shadow-lg');
        }
    });
}

// ======================================
// Mobile Menu Toggle
// ======================================
const mobileMenuToggle = document.getElementById('mobile-menu-toggle');
const mobileMenu = document.getElementById('mobile-menu');
const mobileMenuClose = document.getElementById('mobile-menu-close');
const mobileMenuOverlay = document.getElementById('mobile-menu-overlay');

function openMobileMenu() {
    if (mobileMenu && mobileMenuOverlay) {
        mobileMenu.classList.remove('translate-x-full');
        mobileMenuOverlay.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
    }
}

function closeMobileMenu() {
    if (mobileMenu && mobileMenuOverlay) {
        mobileMenu.classList.add('translate-x-full');
        mobileMenuOverlay.classList.add('hidden');
        document.body.style.overflow = '';
    }
}

if (mobileMenuToggle) {
    mobileMenuToggle.addEventListener('click', openMobileMenu);
}
if (mobileMenuClose) {
    mobileMenuClose.addEventListener('click', closeMobileMenu);
}
if (mobileMenuOverlay) {
    mobileMenuOverlay.addEventListener('click', closeMobileMenu);
}

// ======================================
// Mobile Compare Dropdown
// ======================================
const mobileCompareToggle = document.getElementById('mobile-compare-toggle');
const mobileCompareDropdown = document.getElementById('mobile-compare-dropdown');
const mobileCompareArrow = document.querySelector('.mobile-compare-arrow');

mobileCompareToggle?.addEventListener('click', () => {
    mobileCompareDropdown.classList.toggle('hidden');
    mobileCompareArrow.classList.toggle('rotate-180');
});

// ======================================
// Mobile Get Advice Dropdown
// ======================================
const mobileAdviceToggle = document.getElementById('mobile-advice-toggle');
const mobileAdviceDropdown = document.getElementById('mobile-advice-dropdown');
const mobileAdviceArrow = document.querySelector('.mobile-advice-arrow');

mobileAdviceToggle?.addEventListener('click', () => {
    mobileAdviceDropdown.classList.toggle('hidden');
    mobileAdviceArrow.classList.toggle('rotate-180');
});

// ======================================
// Authentication Modals
// ======================================
const signInBtn = document.getElementById('sign-in-btn');
const signUpBtn = document.getElementById('sign-up-btn');
const signinModal = document.getElementById('signin-modal');
const signupModal = document.getElementById('signup-modal');
const signinModalClose = document.getElementById('signin-modal-close');
const signupModalClose = document.getElementById('signup-modal-close');
const switchToSignup = document.getElementById('switch-to-signup');
const switchToSignin = document.getElementById('switch-to-signin');

// Mobile auth buttons
const mobileSignInBtns = document.querySelectorAll('.mobile-sign-in');
const mobileSignUpBtns = document.querySelectorAll('.mobile-sign-up');

function openSignInModal() {
    if (signinModal && signupModal) {
        signinModal.classList.remove('hidden');
        signupModal.classList.add('hidden');
        document.body.style.overflow = 'hidden';
        closeMobileMenu();
    }
}

function openSignUpModal() {
    if (signupModal && signinModal) {
        signupModal.classList.remove('hidden');
        signinModal.classList.add('hidden');
        document.body.style.overflow = 'hidden';
        closeMobileMenu();
    }
}

function closeModals() {
    if (signinModal && signupModal) {
        signinModal.classList.add('hidden');
        signupModal.classList.add('hidden');
        document.body.style.overflow = '';
    }
}

signInBtn?.addEventListener('click', (e) => {
    e.preventDefault();
    openSignInModal();
});

signUpBtn?.addEventListener('click', (e) => {
    e.preventDefault();
    openSignUpModal();
});

mobileSignInBtns.forEach(btn => {
    btn.addEventListener('click', (e) => {
        e.preventDefault();
        openSignInModal();
    });
});

mobileSignUpBtns.forEach(btn => {
    btn.addEventListener('click', (e) => {
        e.preventDefault();
        openSignUpModal();
    });
});

signinModalClose?.addEventListener('click', closeModals);
signupModalClose?.addEventListener('click', closeModals);
switchToSignup?.addEventListener('click', (e) => {
    e.preventDefault();
    openSignUpModal();
});
switchToSignin?.addEventListener('click', (e) => {
    e.preventDefault();
    openSignInModal();
});

// Close modals on overlay click
signinModal?.addEventListener('click', (e) => {
    if (e.target === signinModal) closeModals();
});
signupModal?.addEventListener('click', (e) => {
    if (e.target === signupModal) closeModals();
});

// ======================================
// Form Submissions (Placeholder)
// ======================================
const signinForm = document.getElementById('signin-form');
const signupForm = document.getElementById('signup-form');

signinForm?.addEventListener('submit', (e) => {
    e.preventDefault();
    // Add your sign in logic here
    console.log('Sign In submitted');
    alert('Sign In functionality - Connect to Django backend');
});

signupForm?.addEventListener('submit', (e) => {
    e.preventDefault();
    const password = document.getElementById('signup-password').value;
    const confirmPassword = document.getElementById('signup-confirm-password').value;
    
    if (password !== confirmPassword) {
        alert('Passwords do not match!');
        return;
    }
    
    // Add your sign up logic here
    console.log('Sign Up submitted');
    alert('Sign Up functionality - Connect to Django backend');
});

// ======================================
// Scroll to Top Button
// ======================================
const scrollToTopBtn = document.getElementById('scroll-to-top');

if (scrollToTopBtn) {
    window.addEventListener('scroll', () => {
        if (window.scrollY > 300) {
            scrollToTopBtn.classList.remove('hidden');
        } else {
            scrollToTopBtn.classList.add('hidden');
        }
    });

    scrollToTopBtn.addEventListener('click', () => {
        window.scrollTo({
            top: 0,
            behavior: 'smooth'
        });
    });
}

// ======================================
// Initialize Slick Carousel (if needed)
// ======================================
// Commented out - jQuery not loaded
// $(document).ready(function() {
//     // Conferences Carousel
//     $('#conferences-carousel').slick({
//         dots: true,
//         infinite: true,
//         speed: 500,
//         slidesToShow: 1,
//         slidesToScroll: 1,
//         autoplay: true,
//         autoplaySpeed: 3000,
//         arrows: true,
//         responsive: [
//             {
//                 breakpoint: 768,
//                 settings: {
//                     arrows: false
//                 }
//             }
//         ]
//     });
//     
//     // Add more carousel initializations as needed
// });
