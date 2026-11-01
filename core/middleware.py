from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import timezone
from django.urls import reverse

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
CSP_ADMIN = CSP.replace("style-src 'self'", "style-src 'self' 'unsafe-inline'")


class SecurityHeadersMiddleware:
    """CSP + no-store caching (patient data must never sit in a browser/proxy cache)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        is_admin = request.path.startswith("/" + settings.ADMIN_URL)
        response.headers.setdefault("Content-Security-Policy", CSP_ADMIN if is_admin else CSP)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"
        return response


class ForcePasswordChangeMiddleware:
    """Users with a temporary/reset password can do nothing else until they choose their own."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        u = getattr(request, "user", None)
        if u is not None and u.is_authenticated and getattr(u, "must_change_password", False):
            issued = getattr(u, "temp_password_issued_at", None)
            if issued and timezone.now() - issued > timedelta(hours=settings.TEMP_PASSWORD_HOURS):
                logout(request)
                messages.error(request, "Your temporary password has expired. Ask the administrator to reset it.")
                return redirect("accounts:login")
            allowed = {reverse("accounts:password_change"), reverse("accounts:logout")}
            if request.path not in allowed and not request.path.startswith("/static/"):
                return redirect("accounts:password_change")
        return self.get_response(request)
