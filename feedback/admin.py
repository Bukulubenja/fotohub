from django.contrib import admin

from feedback.models import Feedback


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ["created_at", "short_message", "name", "contact", "handled"]
    list_filter = ["handled"]
    list_editable = ["handled"]
    search_fields = ["message", "name", "contact"]
    readonly_fields = ["message", "name", "contact", "page", "created_at"]

    @admin.display(description="Message")
    def short_message(self, feedback: Feedback) -> str:
        return str(feedback)
