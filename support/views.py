from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from accounts.models import Role
from accounts.permissions import RoleRequiredMixin
from core.audit import log, save_form
from core.models import AuditLog

from .forms import ReplyForm, TicketForm
from .models import Ticket, TicketMessage

MAX_ACTIVE_PER_USER = 10


class AnyRole(RoleRequiredMixin):
    allowed_roles = (Role.ADMIN, Role.DOCTOR, Role.PHARMACIST)


class Requesters(RoleRequiredMixin):
    allowed_roles = (Role.DOCTOR, Role.PHARMACIST)


def _visible(user):
    qs = Ticket.objects.select_related("created_by")
    return qs if user.is_admin_role else qs.filter(created_by=user)  # users can only ever see their own


class TicketList(AnyRole, View):
    def get(self, request):
        qs = _visible(request.user)
        st = request.GET.get("status", "active")
        if st == "active":
            qs = qs.filter(status__in=[Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS])
        elif st in Ticket.Status.values:
            qs = qs.filter(status=st)
        cat = request.GET.get("category")
        if cat in Ticket.Category.values:
            qs = qs.filter(category=cat)
        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(subject__icontains=q) | Q(created_by__username__icontains=q))
        page = Paginator(qs, 25).get_page(request.GET.get("page"))
        return render(request, "support/ticket_list.html", {"page": page, "st": st, "cat": cat, "q": q,
                                                            "statuses": Ticket.Status.choices, "categories": Ticket.Category.choices})


class TicketCreate(Requesters, View):
    def get(self, request):
        return render(request, "support/ticket_form.html", {"form": TicketForm()})

    def post(self, request):
        form = TicketForm(request.POST)
        busy = Ticket.objects.filter(created_by=request.user, status__in=[Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS]).count()
        if busy >= MAX_ACTIVE_PER_USER:
            form.add_error(None, f"You already have {busy} open tickets. Please wait for them to be resolved or close some.")
        elif form.is_valid():
            t, _ = save_form(request, form)
            messages.success(request, f"Ticket {t.ref} sent to the administrator.")
            return redirect("support:detail", pk=t.pk)
        return render(request, "support/ticket_form.html", {"form": form})


class TicketDetail(AnyRole, View):
    def _ctx(self, request, t, form):
        return {"t": t, "msgs": t.messages.select_related("author"), "form": form, "is_admin": request.user.is_admin_role}

    def get(self, request, pk):
        t = get_object_or_404(_visible(request.user), pk=pk)
        # opening the ticket marks it read for the viewer (queryset.update: no edit-stamp change)
        flag = "admin_unread" if request.user.is_admin_role else "user_unread"
        if getattr(t, flag):
            Ticket.objects.filter(pk=t.pk).update(**{flag: False})
        return render(request, "support/ticket_detail.html", self._ctx(request, t, ReplyForm(is_admin=request.user.is_admin_role, current=t.status)))

    def post(self, request, pk):
        admin = request.user.is_admin_role
        with transaction.atomic():
            t = get_object_or_404(_visible(request.user).select_for_update(of=("self",)), pk=pk)
            form = ReplyForm(request.POST, is_admin=admin, current=t.status)
            if not form.is_valid():
                return render(request, "support/ticket_detail.html", self._ctx(request, t, form))
            body, old = form.cleaned_data["body"].strip(), t.status
            if body:
                TicketMessage.objects.create(ticket=t, author=request.user, is_admin=admin, body=body)
            if admin:
                new = form.cleaned_data.get("status") or old
                if new == old and old == Ticket.Status.OPEN and body:
                    new = Ticket.Status.IN_PROGRESS
                t.status, t.user_unread, t.admin_unread = new, True, False
            else:
                t.admin_unread, t.user_unread = True, False
                if t.status in (Ticket.Status.RESOLVED, Ticket.Status.CLOSED):
                    t.status = Ticket.Status.OPEN  # a reply reopens it
            t.resolved_at = timezone.now() if t.status == Ticket.Status.RESOLVED else (None if t.is_active else t.resolved_at)
            t.last_activity, t.updated_by, t.version = timezone.now(), request.user, t.version + 1
            t.save()
            log(request, AuditLog.Action.UPDATE, t, changed=["status"] if t.status != old else [],
                detail=("Reply; " if body else "") + (f"status {old} -> {t.status}" if t.status != old else ""))
        messages.success(request, "Reply sent." if body else "Status updated.")
        return redirect("support:detail", pk=pk)


class TicketClose(Requesters, View):
    def post(self, request, pk):
        t = get_object_or_404(Ticket, pk=pk, created_by=request.user)
        if t.status != Ticket.Status.CLOSED:
            t.status, t.updated_by, t.version, t.last_activity = Ticket.Status.CLOSED, request.user, t.version + 1, timezone.now()
            t.admin_unread = True
            t.save()
            log(request, AuditLog.Action.UPDATE, t, changed=["status"], detail="Closed by requester")
            messages.success(request, f"Ticket {t.ref} closed.")
        return redirect("support:detail", pk=pk)
