"""Create the very first admin master from environment variables (for hosts with no shell, e.g. Render free/starter).

Set ADMIN_BOOTSTRAP_USERNAME and ADMIN_BOOTSTRAP_PASSWORD, deploy, sign in, choose your own password
(forced at first login), then DELETE ADMIN_BOOTSTRAP_PASSWORD from the environment.
Does nothing once any admin exists, so it can safely run on every start.
"""
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from accounts.models import Role


class Command(BaseCommand):
    help = __doc__

    def handle(self, *args, **opts):
        User = get_user_model()
        name, pw = os.environ.get("ADMIN_BOOTSTRAP_USERNAME", "").strip(), os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")
        if User.objects.filter(role=Role.ADMIN).exists():
            if pw:
                self.stdout.write(self.style.WARNING("An admin already exists – remove ADMIN_BOOTSTRAP_PASSWORD from the environment."))
            return
        if not name or not pw:
            self.stdout.write("No admin exists yet and ADMIN_BOOTSTRAP_USERNAME / ADMIN_BOOTSTRAP_PASSWORD are not set.")
            return
        try:
            validate_password(pw, user=User(username=name))
        except ValidationError as e:
            raise SystemExit("ADMIN_BOOTSTRAP_PASSWORD rejected: " + " ".join(e.messages))
        User.objects.create_user(username=name, password=pw, role=Role.ADMIN, must_change_password=True)
        self.stdout.write(self.style.SUCCESS(f"Created admin '{name}'. Sign in, set a new password, then remove ADMIN_BOOTSTRAP_PASSWORD."))
