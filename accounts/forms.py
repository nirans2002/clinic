import secrets
import string

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm

from clinic.validators import normalize_phone

User = get_user_model()


def temp_password(n=16):
    alphabet = string.ascii_letters.replace("l", "").replace("I", "").replace("O", "") + string.digits.replace("0", "").replace("1", "")
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            return pw


class LoginForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username", "autocapitalize": "none"}))
    password = forms.CharField(strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    error_messages = {
        "invalid_login": "Incorrect username or password.",  # identical message for all failures (no user enumeration)
        "inactive": "Incorrect username or password.",
    }


class StyledPasswordChangeForm(PasswordChangeForm):
    pass


class _PhoneMixin:
    def clean_phone(self):
        p = (self.cleaned_data.get("phone") or "").strip()
        return normalize_phone(p) if p else ""


PHONE_WIDGET = forms.TextInput(attrs={"type": "tel", "inputmode": "tel", "maxlength": 18, "placeholder": "10-digit mobile"})


class UserCreateForm(_PhoneMixin, forms.ModelForm):
    send_email = forms.BooleanField(required=False, label="E-mail the temporary password to the user")

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "phone", "role"]
        widgets = {"phone": PHONE_WIDGET}

    def __init__(self, *args, email_enabled=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not email_enabled:
            del self.fields["send_email"]

    def clean(self):
        d = super().clean()
        if d.get("send_email") and not d.get("email"):
            self.add_error("email", "Enter an e-mail address to send the password by e-mail.")
        return d

    def clean_username(self):
        u = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=u).exists():
            raise forms.ValidationError("This username is already taken.")
        return u


class UserEditForm(_PhoneMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone", "role", "is_active"]
        widgets = {"phone": PHONE_WIDGET}
