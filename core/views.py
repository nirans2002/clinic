import csv
from datetime import datetime, time, timedelta

from django.core.paginator import Paginator
from django.http import StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from accounts.permissions import AdminOnly

from .audit import log
from .models import AuditLog


def permission_denied(request, exception=None):
    return render(request, "403.html", status=403)


def not_found(request, exception=None):
    return render(request, "404.html", status=404)


def server_error(request):
    return render(request, "500.html", status=500)


def _filtered(request):
    qs = AuditLog.objects.select_related("user")
    g = request.GET
    if g.get("user"):
        qs = qs.filter(username__icontains=g["user"].strip())
    if g.get("action"):
        qs = qs.filter(action=g["action"])
    if g.get("q"):
        qs = qs.filter(object_repr__icontains=g["q"].strip())
    tz = timezone.get_current_timezone()
    try:
        if g.get("from"):
            qs = qs.filter(timestamp__gte=datetime.combine(datetime.strptime(g["from"], "%Y-%m-%d"), time.min, tz))
        if g.get("to"):
            qs = qs.filter(timestamp__lt=datetime.combine(datetime.strptime(g["to"], "%Y-%m-%d") + timedelta(days=1), time.min, tz))
    except ValueError:
        pass
    return qs


class AuditList(AdminOnly, View):
    def get(self, request):
        page = Paginator(_filtered(request), 50).get_page(request.GET.get("page"))
        params = request.GET.copy()
        params.pop("page", None)
        return render(
            request,
            "core/audit_list.html",
            {"page": page, "actions": AuditLog.Action.choices, "qs": params.urlencode(), "f": request.GET},
        )


class _Echo:
    def write(self, v):
        return v


class AuditExport(AdminOnly, View):
    def get(self, request):
        log(request, AuditLog.Action.EXPORT, detail="Audit log CSV export")
        w = csv.writer(_Echo())

        def rows():
            yield w.writerow(["time", "user", "role", "action", "record", "record_id", "detail", "fields_changed", "ip"])
            for a in _filtered(request).iterator(chunk_size=500):
                cells = [a.timestamp.astimezone().isoformat(timespec="seconds"), a.username, a.role, a.action, a.model,
                         a.object_id, a.detail, a.changed_fields, a.ip_address or ""]
                # neutralise spreadsheet formula injection
                yield w.writerow([("'" + c) if isinstance(c, str) and c[:1] in "=+-@" else c for c in cells])

        resp = StreamingHttpResponse(rows(), content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="audit-log.csv"'
        return resp
