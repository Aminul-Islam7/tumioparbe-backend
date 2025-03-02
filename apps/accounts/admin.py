from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from simple_history.admin import SimpleHistoryAdmin

from apps.accounts.models import User, Student


@admin.register(User)
class UserAdmin(BaseUserAdmin, SimpleHistoryAdmin):
    list_display = ('name', 'phone', 'facebook_profile_link', 'address_summary', 'is_admin', 'is_staff', 'created_at')
    list_filter = ('is_admin', 'is_staff', 'is_active', 'created_at')
    search_fields = ('name', 'phone', 'address', 'facebook_profile')
    ordering = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    history_list_display = ['name', 'phone', 'is_admin']

    fieldsets = (
        (None, {'fields': ('name', 'phone', 'password')}),
        (_('Contact Information'), {'fields': ('address', 'facebook_profile',)}),
        (_('Permissions'), {'fields': ('is_active', 'is_admin', 'is_staff', 'is_superuser',
                                       'groups', 'user_permissions')}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined', 'created_at', 'updated_at')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('phone', 'name', 'address', 'facebook_profile', 'password1', 'password2'),
        }),
    )

    def facebook_profile_link(self, obj):
        if obj.facebook_profile:
            return format_html('<a href="{}" target="_blank">Link</a>', obj.facebook_profile)
        return "-"
    facebook_profile_link.short_description = 'Facebook'

    def address_summary(self, obj):
        if obj.address and len(obj.address) > 30:
            return obj.address[:30] + "..."
        return obj.address
    address_summary.short_description = 'Address'


@admin.register(Student)
class StudentAdmin(SimpleHistoryAdmin):
    list_display = ('name', 'parent_info', 'date_of_birth', 'school', 'current_class', 'created_at')
    list_filter = ('date_of_birth', 'school', 'current_class', 'created_at')
    search_fields = ('name', 'school', 'parent__name', 'parent__phone')
    raw_id_fields = ('parent',)
    readonly_fields = ('created_at', 'updated_at', 'parent_detail_link')
    history_list_display = ['name', 'school', 'current_class']

    fieldsets = (
        ('Student Information', {
            'fields': ('name', 'date_of_birth', 'school', 'current_class')
        }),
        ('Parent Information', {
            'fields': ('parent', 'parent_detail_link', 'father_name', 'mother_name')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def parent_info(self, obj):
        if obj.parent:
            url = reverse('admin:accounts_user_change', args=[obj.parent.id])
            return format_html('<a href="{}">{} ({})</a>', url, obj.parent.name, obj.parent.phone)
        return "-"
    parent_info.short_description = 'Parent'

    def parent_detail_link(self, obj):
        if obj.parent:
            url = reverse('admin:accounts_user_change', args=[obj.parent.id])
            return format_html('<a href="{}" class="button">View Parent Details</a>', url)
        return "-"
    parent_detail_link.short_description = 'Parent Details'
