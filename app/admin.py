from django.contrib import admin
from django.utils import timezone
from ckeditor.widgets import CKEditorWidget
from django import forms
from .models import University, Course, Scholarship, Subject, Tuition, UniversityRanking, BlogPost, BlogCategory, Comment, ExchangeRate

class BlogPostAdminForm(forms.ModelForm):
    content = forms.CharField(widget=CKEditorWidget(config_name='default'))
    meta_description = forms.CharField(widget=forms.Textarea(attrs={'rows': 3, 'cols': 80}), required=False, help_text='SEO meta description (recommended 150-160 characters)')
    
    class Meta:
        model = BlogPost
        fields = ['title', 'author', 'category', 'meta_description', 'content', 'featured_image', 'status']

@admin.register(BlogCategory)
class BlogCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    form = BlogPostAdminForm
    list_display = ('title', 'author', 'category', 'status', 'created_at')
    list_filter = ('status', 'category', 'created_at')
    search_fields = ('title', 'content', 'author')
    ordering = ('-created_at',)
    
    fields = ('title', 'author', 'category', 'meta_description', 'content', 'featured_image', 'status')

@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('name', 'post', 'email', 'is_approved', 'created_at')
    list_filter = ('is_approved', 'created_at')
    search_fields = ('name', 'email', 'comment', 'post__title')
    actions = ['approve_comments', 'unapprove_comments']
    
    def approve_comments(self, request, queryset):
        queryset.update(is_approved=True)
    approve_comments.short_description = 'Approve selected comments'
    
    def unapprove_comments(self, request, queryset):
        queryset.update(is_approved=False)
    unapprove_comments.short_description = 'Unapprove selected comments'

@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ('currency_code', 'currency_name', 'currency_symbol', 'rate_to_gbp', 'is_active', 'updated_at')
    list_filter = ('is_active', 'updated_at')
    search_fields = ('currency_code', 'currency_name')
    list_editable = ('rate_to_gbp', 'is_active')
    ordering = ('currency_code',)
    
    fieldsets = (
        ('Currency Information', {
            'fields': ('currency_code', 'currency_name', 'currency_symbol')
        }),
        ('Exchange Rate', {
            'fields': ('rate_to_gbp', 'is_active'),
            'description': 'Set the exchange rate relative to 1 GBP. For example, if 1 GBP = 1.27 USD, enter 1.27 for USD.'
        }),
    )

# Register your models here.
