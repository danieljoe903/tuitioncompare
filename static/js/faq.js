// FAQ Accordion functionality
document.addEventListener('DOMContentLoaded', function() {
    console.log('FAQ script loaded');
    const faqItems = document.querySelectorAll('.faq-item');
    console.log('Found FAQ items:', faqItems.length);
    
    faqItems.forEach((item, index) => {
        const question = item.querySelector('.faq-question');
        const answer = item.querySelector('.faq-answer');
        const icon = item.querySelector('.faq-icon');
        
        if (!question || !answer || !icon) {
            console.error('Missing elements in FAQ item', index);
            return;
        }
        
        // Set initial state - ensure all are closed
        answer.style.maxHeight = '0px';
        
        question.addEventListener('click', function(e) {
            e.preventDefault();
            console.log('Clicked FAQ item', index);
            
            // Check if this item is currently open
            const isOpen = answer.classList.contains('active');
            
            // Close all items
            faqItems.forEach(otherItem => {
                const otherAnswer = otherItem.querySelector('.faq-answer');
                const otherIcon = otherItem.querySelector('.faq-icon');
                if (otherAnswer && otherIcon) {
                    otherAnswer.style.maxHeight = '0px';
                    otherAnswer.classList.remove('active');
                    otherIcon.classList.remove('rotate-180');
                }
            });
            
            // If it wasn't open, open it
            if (!isOpen) {
                // Use setTimeout to ensure transition works
                setTimeout(() => {
                    answer.style.maxHeight = answer.scrollHeight + 'px';
                }, 10);
                answer.classList.add('active');
                icon.classList.add('rotate-180');
                console.log('Opened FAQ item', index, 'with height:', answer.scrollHeight);
            }
        });
    });
});
