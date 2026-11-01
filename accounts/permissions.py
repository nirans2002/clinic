from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from core.audit import log
from core.models import AuditLog

from .models import Role


class RoleRequiredMixin(LoginRequiredMixin):
    """Class-based views declare `allowed_roles`. Anything else -> 403 + audit entry."""

    allowed_roles = ()
    raise_exception = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role not in self.allowed_roles:
            log(request, AuditLog.Action.DENIED, detail=request.path[:300])
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class DoctorOnly(RoleRequiredMixin):
    allowed_roles = (Role.DOCTOR,)


class PharmacistOnly(RoleRequiredMixin):
    allowed_roles = (Role.PHARMACIST,)


class AdminOnly(RoleRequiredMixin):
    allowed_roles = (Role.ADMIN,)


class ClinicalStaff(RoleRequiredMixin):
    """Doctors and pharmacists (not the master admin)."""

    allowed_roles = (Role.DOCTOR, Role.PHARMACIST)


class CatalogueManagers(RoleRequiredMixin):
    allowed_roles = (Role.DOCTOR, Role.ADMIN)
