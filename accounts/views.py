from axes.utils import reset as axes_reset
from django.conf import settings
from django.core.mail import send_mail
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from urllib.parse import quote
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from core.audit import log
from core.models import AuditLog

from .forms import LoginForm, StyledPasswordChangeForm, UserCreateForm, UserEditForm, temp_password
from .models import Role
from .permissions import AdminOnly

User = get_user_model()


class Login(LoginView):
    template_name = "registration/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class Logout(LogoutView):
    http_method_names = ["post", "options"]


class ChangePassword(PasswordChangeView):
    template_name = "accounts/profile.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("clinic:dashboard")

    def form_valid(self, form):
        resp = super().form_valid(form)
        u = self.request.user
        if u.must_change_password:
            u.must_change_password = False
            u.temp_password_issued_at = None
            u.save(update_fields=["must_change_password", "temp_password_issued_at"])
        log(self.request, AuditLog.Action.PASSWORD, u, detail="Password changed by user")
        messages.success(self.request, "Password updated.")
        return resp


# ----------------------------------------------------------------------------- admin master: users
class UserList(AdminOnly, ListView):
    model = User
    template_name = "accounts/user_list.html"
    context_object_name = "users"

    def get_queryset(self):
        qs = User.objects.all().order_by("role", "username")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
        return qs


def email_enabled():
    return bool(settings.EMAIL_ENABLED) and "email" in settings.CREDENTIAL_DELIVERY


def whatsapp_enabled():
    return "whatsapp" in settings.CREDENTIAL_DELIVERY


def _credential_text(request, user, pw, reset):
    from clinic.models import ClinicSettings

    base = settings.SITE_URL or request.build_absolute_uri("/").rstrip("/")
    what = "password has been reset" if reset else "account is ready"
    return (f"{ClinicSettings.load().name}\nHello {user.get_full_name() or user.username}, your {what}.\n"
            f"Sign in: {base}{reverse('accounts:login')}\nUsername: {user.username}\nTemporary password: {pw}\n"
            f"You must choose your own password at first sign-in (this one expires in {settings.TEMP_PASSWORD_HOURS} hours). "
            "Please delete this message afterwards.")


def _wa_number(phone):
    d = "".join(c for c in (phone or "") if c.isdigit())
    return settings.DEFAULT_COUNTRY_CODE + d if len(d) == 10 else d


def _issue_credentials(request, user, pw, reset, want_email):
    """Render the one-time credentials page; optionally e-mail them. The password is never stored or logged."""
    text = _credential_text(request, user, pw, reset)
    email_result = None
    if want_email and user.email and email_enabled():
        try:
            send_mail(f"Your {text.splitlines()[0]} account", text, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
            email_result = (True, user.email)
            log(request, AuditLog.Action.USER, user, detail="Temporary password sent by e-mail")
        except Exception as e:  # SMTP down / refused – the admin still has the on-screen copy
            email_result = (False, type(e).__name__)
            log(request, AuditLog.Action.USER, user, detail=f"E-mail of temporary password FAILED ({type(e).__name__})")
    wa_url = ""
    if whatsapp_enabled() and user.phone:
        wa_url = f"https://wa.me/{_wa_number(user.phone)}?text={quote(text)}"
    return render(request, "accounts/user_credentials.html",
                  {"u": user, "password": pw, "reset": reset, "email_result": email_result, "wa_url": wa_url})


class UserCreate(AdminOnly, View):
    def get(self, request):
        return render(request, "accounts/user_form.html", {"form": UserCreateForm(email_enabled=email_enabled()), "creating": True,
                                                           "email_enabled": email_enabled(), "wa_enabled": whatsapp_enabled()})

    def post(self, request):
        form = UserCreateForm(request.POST, email_enabled=email_enabled())
        if not form.is_valid():
            return render(request, "accounts/user_form.html", {"form": form, "creating": True,
                                                               "email_enabled": email_enabled(), "wa_enabled": whatsapp_enabled()})
        pw = temp_password()
        user = form.save(commit=False)
        user.set_password(pw)
        user.must_change_password = True
        user.temp_password_issued_at = timezone.now()
        user.created_by = request.user
        user.save()
        log(request, AuditLog.Action.USER, user, detail=f"Created user with role {user.role}")
        return _issue_credentials(request, user, pw, False, form.cleaned_data.get("send_email"))


class UserEdit(AdminOnly, View):
    def _get(self, pk):
        return get_object_or_404(User, pk=pk)

    def get(self, request, pk):
        u = self._get(pk)
        return render(request, "accounts/user_form.html", {"form": UserEditForm(instance=u), "u": u})

    def post(self, request, pk):
        u = self._get(pk)
        form = UserEditForm(request.POST, instance=u)
        if u == request.user and (request.POST.get("is_active") != "on" or request.POST.get("role") != Role.ADMIN):
            messages.error(request, "You cannot deactivate yourself or remove your own admin role.")
            return redirect("accounts:user_edit", pk=pk)
        if form.is_valid():
            if u.role == Role.ADMIN and (form.cleaned_data["role"] != Role.ADMIN or not form.cleaned_data["is_active"]):
                if User.objects.filter(role=Role.ADMIN, is_active=True).exclude(pk=u.pk).count() == 0:
                    messages.error(request, "At least one active admin must remain.")
                    return render(request, "accounts/user_form.html", {"form": form, "u": u})
            form.save()
            if "is_active" in form.changed_data and not u.is_active:
                _kill_sessions(u)
            log(request, AuditLog.Action.USER, u, detail="Edited user", changed=form.changed_data)
            messages.success(request, f"{u.username} updated.")
            return redirect("accounts:user_list")
        return render(request, "accounts/user_form.html", {"form": form, "u": u})


def _kill_sessions(user):
    """Force-logout every live session of a user."""
    from django.contrib.sessions.models import Session

    for s in Session.objects.filter(expire_date__gt=timezone.now()):
        if s.get_decoded().get("_auth_user_id") == str(user.pk):
            s.delete()


class UserResetPassword(AdminOnly, View):
    def get(self, request, pk):
        u = get_object_or_404(User, pk=pk)
        return render(request, "accounts/user_reset_confirm.html", {"u": u, "email_enabled": email_enabled(), "wa_enabled": whatsapp_enabled()})

    def post(self, request, pk):
        u = get_object_or_404(User, pk=pk)
        pw = temp_password()
        u.set_password(pw)
        u.must_change_password = True
        u.temp_password_issued_at = timezone.now()
        u.save()
        _kill_sessions(u)
        axes_reset(username=u.username)
        log(request, AuditLog.Action.USER, u, detail="Password reset by admin; sessions ended")
        return _issue_credentials(request, u, pw, True, request.POST.get("send_email") == "on")


class UserUnlock(AdminOnly, View):
    def post(self, request, pk):
        u = get_object_or_404(User, pk=pk)
        axes_reset(username=u.username)
        log(request, AuditLog.Action.USER, u, detail="Login lockout cleared")
        messages.success(request, f"Lockout cleared for {u.username}.")
        return redirect("accounts:user_list")
