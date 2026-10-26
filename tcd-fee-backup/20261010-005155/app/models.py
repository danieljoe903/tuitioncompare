from django.db import models

class University(models.Model):
    COUNTRY_CHOICES = (("UK", "United Kingdom"), ("IE", "Ireland"))

    university_id = models.CharField(max_length=20, unique=True, primary_key=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    country = models.CharField(max_length=2, choices=COUNTRY_CHOICES)
    city = models.CharField(max_length=100)
    overview = models.TextField()

    estimated_living_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    population = models.PositiveIntegerField(null=True, blank=True)

    admission_requirements = models.TextField(blank=True)
    acceptance_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    graduation_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    avg_ug_tuition = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    avg_pg_tuition = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    currency = models.CharField(max_length=3, default="GBP")
    
    ranking = models.IntegerField(null=True, blank=True)

    financial_aid = models.TextField(blank=True)
    website = models.URLField(blank=True)
    logo = models.URLField(blank=True)
    banner = models.URLField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Universities"

    def __str__(self):
        return self.name

class Subject(models.Model):
    subject_code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name

class Course(models.Model):
    LEVEL_CHOICES = (
        ("UG", "Undergraduate"),
        ("PG", "Postgraduate"),
        ("PGT", "Postgraduate"),
        ("TOPUP", "Top-up"),
        ("MRES", "MRes"),
    )

    course_id = models.CharField(max_length=30, unique=True, primary_key=True)
    university = models.ForeignKey(University, on_delete=models.CASCADE, related_name="courses")
    subject = models.ForeignKey(Subject, on_delete=models.SET_NULL, null=True)

    title = models.CharField(max_length=255)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES)

    overview = models.TextField(blank=True)
    modules = models.TextField(blank=True)
    entry_requirements = models.TextField(blank=True)

    duration = models.CharField(max_length=50)
    location = models.CharField(max_length=255)

    start_year = models.IntegerField()
    start_month = models.CharField(max_length=20)

    is_active = models.BooleanField(default=True)
    ucas_cao_code = models.CharField(max_length=20, blank=True)
    work_placement = models.BooleanField(default=False)
    intake_months = models.CharField(max_length=100)

    course_url = models.URLField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} ({self.university.name})"

class Tuition(models.Model):
    RESIDENCY_CHOICES = (
        ("HOME", "Home"),
        ("EU", "EU"),
        ("INTL", "International"),
    )

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="tuitions"
    )
    residency = models.CharField(max_length=5, choices=RESIDENCY_CHOICES)

    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2)
    scholarship = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )
    tuition_deposit = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    currency = models.CharField(max_length=10)

    @property
    def net_tuition_fee(self):
        return self.tuition_fee - self.scholarship

class UniversityRanking(models.Model):
    SOURCE_CHOICES = (
        ("CUG", "Complete University Guide"),
        ("QS", "Top Universities (QS)"),
    )

    university = models.ForeignKey(
        University, on_delete=models.CASCADE, related_name="rankings"
    )
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    year = models.IntegerField()
    
    overall = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    world_ranking = models.IntegerField(null=True, blank=True)

    arts_humanities = models.IntegerField(null=True, blank=True)
    life_sciences = models.IntegerField(null=True, blank=True)
    engineering_technology = models.IntegerField(null=True, blank=True)
    natural_sciences = models.IntegerField(null=True, blank=True)
    social_sciences_management = models.IntegerField(null=True, blank=True)

    staff_student_ratio = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    international_students_ratio = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    graduate_prospects = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    student_satisfaction = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )

class Scholarship(models.Model):
    LEVEL_CHOICES = (
        ("UG", "Undergraduate"),
        ("PG", "Postgraduate"),
        ("TOPUP", "Top-up"),
        ("MRES", "MRes"),
    )

    university = models.ForeignKey(
        University,
        on_delete=models.CASCADE,
        related_name="scholarships",
        null=True,
        blank=True
    )

    name = models.CharField(max_length=255)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES)

    eligibility = models.TextField()
    award_type = models.CharField(max_length=100)
    award_value = models.CharField(max_length=50, blank=True)
    currency = models.CharField(max_length=3)

    deadline = models.DateField(null=True, blank=True)
    requirements = models.TextField(blank=True)
    description = models.TextField(blank=True)

    url = models.URLField(blank=True)

class BlogCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    
    class Meta:
        verbose_name_plural = 'Blog Categories'
    
    def __str__(self):
        return self.name

class BlogPost(models.Model):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('published', 'Published'),
    )
    
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True)
    author = models.CharField(max_length=100, default='Admin')
    category = models.ForeignKey(BlogCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='posts')
    meta_description = models.TextField(blank=True, help_text='SEO meta description (recommended 150-160 characters)')
    excerpt = models.TextField(max_length=500, blank=True, editable=False)
    content = models.TextField()
    featured_image = models.ImageField(upload_to='blog/', blank=True, null=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)
    
    views = models.PositiveIntegerField(default=0)
    
    class Meta:
        ordering = ['-published_at', '-created_at']
        verbose_name = 'Blog Post'
        verbose_name_plural = 'Blog Posts'
    
    def save(self, *args, **kwargs):
        # Auto-generate slug from title
        if not self.slug:
            from django.utils.text import slugify
            base_slug = slugify(self.title)
            slug = base_slug
            counter = 1
            while BlogPost.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        
        # Auto-generate excerpt from content (first 200 characters)
        if not self.excerpt and self.content:
            from django.utils.html import strip_tags
            clean_content = strip_tags(self.content)
            self.excerpt = clean_content[:200] + ('...' if len(clean_content) > 200 else '')
        
        # Auto-set published_at when status changes to published
        if self.status == 'published' and not self.published_at:
            from django.utils import timezone
            self.published_at = timezone.now()
        # Clear published_at if status changes to draft
        elif self.status == 'draft':
            self.published_at = None
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.title

class Comment(models.Model):
    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name='comments')
    name = models.CharField(max_length=100)
    email = models.EmailField()
    comment = models.TextField()
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f'Comment by {self.name} on {self.post.title}'


class ExchangeRate(models.Model):
    CURRENCY_CHOICES = [
        ('GBP', 'British Pound (£)'),
        ('EUR', 'Euro (€)'),
        ('USD', 'US Dollar ($)'),
        ('NGN', 'Nigerian Naira (₦)'),
        ('GHS', 'Ghanaian Cedi (₵)'),
        ('KES', 'Kenyan Shilling (KSh)'),
        ('ZAR', 'South African Rand (R)'),
        ('INR', 'Indian Rupee (₹)'),
        ('CNY', 'Chinese Yuan (¥)'),
        ('PKR', 'Pakistani Rupee (Rs)'),
        ('BDT', 'Bangladeshi Taka (৳)'),
    ]
    
    currency_code = models.CharField(max_length=3, choices=CURRENCY_CHOICES, unique=True)
    currency_name = models.CharField(max_length=100)
    currency_symbol = models.CharField(max_length=10)
    rate_to_gbp = models.DecimalField(
        max_digits=10, 
        decimal_places=4,
        help_text="Exchange rate relative to 1 GBP (e.g., if 1 GBP = 1.27 USD, enter 1.27)"
    )
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['currency_code']
        verbose_name = 'Exchange Rate'
        verbose_name_plural = 'Exchange Rates'
    
    def __str__(self):
        return f"{self.currency_code} - {self.currency_name} (1 GBP = {self.rate_to_gbp} {self.currency_code})"




