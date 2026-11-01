from django.conf import settings

from .models import AuditLog


def client_ip(request):
    if request is None:
        return None
    if settings.TRUST_PROXY:
        fwd = request.META.get("HTTP_X_FORWARDED_FOR")
        if fwd:
            return fwd.split(",")[-1].strip() or None  # last hop = added by OUR proxy
    return request.META.get("REMOTE_ADDR") or None


def log(request, action, obj=None, detail="", changed=None, user=None, username=""):
    """Write one audit entry. `changed` = list of field NAMES (values are never logged)."""
    user = user or (getattr(request, "user", None) if request is not None else None)
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    entry = AuditLog(
        user=user,
        username=(user.username if user else username)[:150],
        role=(getattr(user, "role", "") if user else ""),
        action=action,
        detail=(detail or "")[:300],
        changed_fields=", ".join(sorted(set(changed or [])))[:300],
        ip_address=client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:200] if request is not None else ""),
    )
    if obj is not None:
        entry.model = obj.__class__.__name__
        entry.object_id = str(obj.pk or "")
        entry.object_repr = str(obj)[:200]
    entry.save()
    return entry


def save_form(request, form):
    """Save a ModelForm of an AuditedModel, stamping created_by/updated_by and logging it."""
    obj = form.save(commit=False)
    is_new = obj.pk is None
    if is_new:
        obj.created_by = request.user
    else:
        obj.version += 1
    obj.updated_by = request.user
    obj.save()
    form.save_m2m()
    changed = [] if is_new else [f for f in form.changed_data if f != "version"]
    log(request, AuditLog.Action.CREATE if is_new else AuditLog.Action.UPDATE, obj, changed=changed)
    return obj, is_new


def trail_for(obj, limit=15):
    return AuditLog.objects.filter(model=obj.__class__.__name__, object_id=str(obj.pk)).select_related("user")[:limit]
