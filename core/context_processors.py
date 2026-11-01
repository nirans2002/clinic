from django.conf import settings


def app(request):
    ctx = {"IDLE_TIMEOUT_SECONDS": settings.IDLE_TIMEOUT_MINUTES * 60, "WHATSAPP_ENABLED": settings.ENABLE_WHATSAPP_SHARE}
    try:
        from clinic.models import ClinicSettings

        ctx["BIZ"] = ClinicSettings.load()
    except Exception:  # during migrate / before tables exist
        ctx["BIZ"] = None
    u = getattr(request, "user", None)
    ctx["SUPPORT_BADGE"] = 0
    if u is not None and u.is_authenticated:
        try:
            from support.models import Ticket

            ctx["SUPPORT_BADGE"] = (Ticket.objects.filter(admin_unread=True) if u.is_admin_role
                                    else Ticket.objects.filter(created_by=u, user_unread=True)).count()
        except Exception:
            pass
    return ctx
