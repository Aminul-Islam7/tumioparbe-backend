from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DefaultUserAdmin
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User, Student


@admin.register(User)
class UserAdmin(DefaultUserAdmin):
    """Custom admin for User model"""
    list_display = ('phone', 'name', 'is_admin', 'is_staff', 'date_joined')
    search_fields = ('phone', 'name', 'email')
    list_filter = ('is_admin', 'is_staff', 'is_superuser', 'is_active', 'date_joined')
    fieldsets = (
        (None, {'fields': ('phone', 'password')}),
        (_('Personal info'), {'fields': ('name', 'address', 'facebook_profile', 'email')}),
        (_('Permissions'), {'fields': ('is_active', 'is_admin', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('phone', 'password1', 'password2'),
        }),
    )
    ordering = ('phone',)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    """Custom admin for Student model"""
    list_display = ('name', 'parent_name', 'current_class', 'school', 'date_of_birth')
    list_filter = ('current_class', 'school')
    search_fields = ('name', 'parent__name', 'parent__phone', 'school')

    def parent_name(self, obj):
        return obj.parent.name
    parent_name.short_description = 'Parent'
