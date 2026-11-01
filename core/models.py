from django.conf import settings
from django.db import models


class AuditedModel(models.Model):
    """Every shared record remembers who added it, who last edited it, and when."""

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )
    # optimistic-locking counter: stops two doctors silently overwriting each other
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        abstract = True


class AuditLog(models.Model):
    """Append-only security / activity trail. Never edited or deleted through the app."""

    class Action(models.TextChoices):
        LOGIN = "login", "Login"
        LOGIN_FAILED = "login_failed", "Login failed"
        LOCKOUT = "lockout", "Account locked"
        LOGOUT = "logout", "Logout"
        CREATE = "create", "Created"
        UPDATE = "update", "Edited"
        VIEW = "view", "Viewed"
        PRINT = "print", "Printed"
        SHARE = "share", "Shared"
        APPROVE = "approve", "Approved Rx"
        DISPENSE = "dispense", "Dispensed"
        VOID = "void", "Voided"
        USER = "user", "User management"
        PASSWORD = "password", "Password"
        DENIED = "denied", "Access denied"
        EXPORT = "export", "Export"

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    username = models.CharField(max_length=150, blank=True)  # snapshot, survives user deletion
    role = models.CharField(max_length=12, blank=True)
    action = models.CharField(max_length=14, choices=Action.choices, db_index=True)
    model = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    detail = models.CharField(max_length=300, blank=True)
    changed_fields = models.CharField(max_length=300, blank=True)  # names only – never PHI values
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-timestamp", "-id"]
        indexes = [models.Index(fields=["model", "object_id"])]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.username} {self.action} {self.model}#{self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Audit log entries are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit log entries cannot be deleted.")
