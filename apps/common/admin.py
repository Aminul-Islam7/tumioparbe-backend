from django.contrib import admin
from django.utils.safestring import mark_safe
import json
from apps.common.models import ActivityLog, SMSLog, SystemSettings


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action_type', 'metadata_summary', 'created_at')
    list_filter = ('action_type', 'created_at', 'user')
    search_fields = ('user__name', 'user__phone', 'action_type', 'metadata')
    date_hierarchy = 'created_at'
    readonly_fields = ('created_at', 'metadata_display')

    def metadata_summary(self, obj):
        """Show a brief summary of metadata in the list view"""
        if not obj.metadata:
            return "-"

        # Try to extract the most relevant info based on action_type
        if obj.action_type == 'PAYMENT':
            if 'amount' in obj.metadata:
                return f"Amount: ৳{obj.metadata.get('amount')}, Status: {obj.metadata.get('status', 'N/A')}"
            elif 'invoice_count' in obj.metadata:
                return f"Bulk payment of {obj.metadata.get('invoice_count')} invoices"

        elif obj.action_type == 'ENROLLMENT':
            student_name = obj.metadata.get('student_name', 'Unknown')
            course = obj.metadata.get('course', 'Unknown')
            batch = obj.metadata.get('batch', 'Unknown')
            return f"Student: {student_name}, Course: {course}, Batch: {batch}"

        elif obj.action_type == 'FEE_MODIFICATION':
            return f"Invoice #{obj.metadata.get('invoice_id', 'N/A')}, Amount: ৳{obj.metadata.get('amount', 'N/A')}"

        elif obj.action_type == 'BATCH_TRANSFER':
            student_name = obj.metadata.get('student_name', 'Unknown')
            from_batch = obj.metadata.get('from_batch', 'Unknown')
            to_batch = obj.metadata.get('to_batch', obj.metadata.get('batch', 'Unknown'))

            if 'action' in obj.metadata and obj.metadata['action'] == 'unenroll':
                return f"Unenrolled: {student_name} from {to_batch}"
            else:
                return f"Transfer: {student_name} from {from_batch} to {to_batch}"

        # For other types, show first key-value pair
        try:
            # Get the first key that's not overly detailed
            exclude_keys = ['description', 'details']
            keys = [k for k in obj.metadata.keys() if k not in exclude_keys]
            if keys:
                first_key = keys[0]
                return f"{first_key}: {obj.metadata[first_key]}"
            return "View details..."
        except:
            return "View details..."

    metadata_summary.short_description = "Summary"

    def metadata_display(self, obj):
        """Display the metadata as formatted JSON"""
        if not obj.metadata:
            return "No metadata"

        formatted_json = json.dumps(obj.metadata, indent=2)
        return mark_safe(f"<pre>{formatted_json}</pre>")

    metadata_display.short_description = "Metadata"

    fieldsets = (
        ('Activity Information', {
            'fields': ('user', 'action_type', 'created_at')
        }),
        ('Metadata', {
            'fields': ('metadata_display',)
        }),
    )


@admin.register(SMSLog)
class SMSLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'phone_number', 'message_type', 'status', 'recipient_count', 'sent_by_display', 'created_at')
    list_filter = ('message_type', 'status', 'created_at')
    search_fields = ('phone_number', 'message', 'sent_by__name', 'sent_by__phone')
    date_hierarchy = 'created_at'
    readonly_fields = ('message_display', 'api_response_display', 'created_at', 'updated_at')

    def sent_by_display(self, obj):
        if obj.sent_by:
            return f"{obj.sent_by.name} ({obj.sent_by.phone})"
        return "System"
    sent_by_display.short_description = "Sent By"

    def message_display(self, obj):
        return obj.message
    message_display.short_description = "Message Content"

    def api_response_display(self, obj):
        if not obj.api_response:
            return "No response data"

        import json
        from django.utils.safestring import mark_safe

        if isinstance(obj.api_response, str):
            try:
                response_data = json.loads(obj.api_response)
            except:
                return obj.api_response
        else:
            response_data = obj.api_response

        # Format the JSON nicely
        formatted_json = json.dumps(response_data, indent=2)
        return mark_safe(f"<pre>{formatted_json}</pre>")
    api_response_display.short_description = "API Response"

    fieldsets = (
        ('Basic Information', {
            'fields': ('phone_number', 'message_type', 'status', 'recipient_count', 'sent_by')
        }),
        ('Message Details', {
            'fields': ('message_display',)
        }),
        ('Results', {
            'fields': ('successful_count', 'failed_count', 'api_response_display')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    """Admin interface for system settings"""
    list_display = ('__str__', 'payment_reminder_days', 'invoice_generation_days',
                    'auto_generate_invoices', 'auto_send_reminders', 'updated_at')
    readonly_fields = ('created_by', 'updated_by', 'created_at', 'updated_at')
    fieldsets = (
        ('Reminder Settings', {
            'fields': ('payment_reminder_days', 'auto_send_reminders'),
            'description': ('Configure which days of the month to send payment reminders (1-28). '
                            'Use comma-separated values like "3,7,15".')
        }),
        ('Invoice Generation', {
            'fields': ('invoice_generation_days', 'auto_generate_invoices'),
            'description': ('Configure how many days before the end of the month to generate '
                            'the next month\'s invoices.')
        }),
        ('System Information', {
            'fields': ('created_by', 'created_at', 'updated_by', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def save_model(self, request, obj, form, change):
        """Track who created or updated the settings"""
        if not obj.pk:  # If creating a new object
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        """Only allow adding if no settings exist yet"""
        return not SystemSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        """Prevent deleting the settings"""
        return False
