from django.conf import settings
from django.db import models
from django.utils import timezone

from core.fields import EncryptedTextField
from core.models import AuditedModel


class Ticket(AuditedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Category(models.TextChoices):
        ACCESS = "access", "Login / access problem"
        DATA = "data", "Data correction"
        BUG = "bug", "Something isn't working"
        REQUEST = "request", "Feature request"
        QUESTION = "question", "Question"
        OTHER = "other", "Other"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    subject = models.CharField(max_length=150)
    category = models.CharField(max_length=10, choices=Category.choices, default=Category.QUESTION)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    description = EncryptedTextField()
    admin_unread = models.BooleanField(default=True)   # admin has something new to read
    user_unread = models.BooleanField(default=False)   # requester has an admin reply to read
    last_activity = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_activity"]

    @property
    def ref(self):
        return f"T-{self.pk:04d}"

    @property
    def is_active(self):
        return self.status in (self.Status.OPEN, self.Status.IN_PROGRESS)

    def __str__(self):
        return f"{self.ref} {self.subject}"[:200]


class TicketMessage(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    is_admin = models.BooleanField(default=False)
    body = EncryptedTextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
