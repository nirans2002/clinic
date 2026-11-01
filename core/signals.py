from axes.signals import user_locked_out
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .audit import log
from .models import AuditLog


@receiver(user_logged_in)
def on_login(sender, request, user, **kw):
    log(request, AuditLog.Action.LOGIN, user=user)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kw):
    if user:
        log(request, AuditLog.Action.LOGOUT, user=user)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kw):
    log(request, AuditLog.Action.LOGIN_FAILED, username=str((credentials or {}).get("username", ""))[:150])


@receiver(user_locked_out)
def on_lockout(sender, request=None, username=None, **kw):
    log(request, AuditLog.Action.LOCKOUT, username=str(username or "")[:150], detail="Too many failed logins")
