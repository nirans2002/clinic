from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    ADMIN = "admin", "Admin (master)"
    DOCTOR = "doctor", "Doctor"
    PHARMACIST = "pharmacist", "Pharmacist"


class User(AbstractUser):
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.DOCTOR)
    phone = models.CharField("Mobile (for WhatsApp)", max_length=20, blank=True)
    must_change_password = models.BooleanField(default=True)
    temp_password_issued_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="created_users")

    class Meta:
        ordering = ["role", "username"]

    def save(self, *args, **kwargs):
        # The admin master role and Django's superuser flag always travel together.
        if self.is_superuser:
            self.role = Role.ADMIN
        if self.role == Role.ADMIN:
            self.is_superuser = True
            self.is_staff = True
        else:
            self.is_superuser = False
            self.is_staff = False
        super().save(*args, **kwargs)

    @property
    def is_admin_role(self):
        return self.role == Role.ADMIN

    @property
    def is_doctor(self):
        return self.role == Role.DOCTOR

    @property
    def is_pharmacist(self):
        return self.role == Role.PHARMACIST

    @property
    def display_name(self):
        name = self.get_full_name().strip() or self.username
        return f"Dr. {name}" if self.is_doctor and not name.lower().startswith("dr") else name

    def __str__(self):
        return self.display_name
