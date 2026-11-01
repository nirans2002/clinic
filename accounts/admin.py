from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class ClinicUserAdmin(UserAdmin):
    """Emergency back-office only; day-to-day user management is in the app (Manage → Users)."""

    list_display = ("username", "first_name", "last_name", "role", "is_active")
    fieldsets = UserAdmin.fieldsets + (("Clinic", {"fields": ("role", "must_change_password")}),)
