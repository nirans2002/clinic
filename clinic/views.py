import re
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from accounts.models import Role
from accounts.permissions import AdminOnly, CatalogueManagers, ClinicalStaff, DoctorOnly, PharmacistOnly
from core.audit import log, save_form, trail_for
from core.models import AuditLog

from .forms import (AppointmentForm, BillForm, BillItemFormSet, CatalogForm, CatalogImportForm, ChronicCaseForm, PatientForm,
                    SettingsForm, VisitForm, VoidForm)
from .models import Appointment, Bill, BillItem, CatalogItem, ChronicCase, ClinicSettings, Patient, Visit

User = get_user_model()
A = AuditLog.Action


def _page(request, qs, per=25):
    return Paginator(qs, per).get_page(request.GET.get("page"))


def _sum(qs, field="total"):
    return qs.aggregate(s=Sum(field))["s"] or Decimal("0")


# ============================================================================ dashboard
class Dashboard(LoginRequiredMixin, View):
    def get(self, request):
        role = request.user.role
        if role == Role.PHARMACIST:
            return redirect("clinic:pharmacy_queue")
        if role == Role.ADMIN:
            return self.admin(request)
        return self.doctor(request)

    def doctor(self, request):
        td = timezone.localdate()
        ctx = {
            "stats": [
                ("Total patients", Patient.objects.count()),
                ("Today's cases", Visit.objects.filter(date=td).count()),
                ("Today's appointments", Appointment.objects.filter(date=td).exclude(status="cancelled").count()),
                ("Waiting at pharmacy", Visit.objects.filter(status=Visit.Status.APPROVED).count()),
            ],
            "appts": Appointment.objects.filter(date=td).exclude(status="cancelled").select_related("patient"),
            "recent": Visit.objects.filter(date=td).select_related("patient", "created_by", "updated_by")[:12],
        }
        return render(request, "clinic/dashboard_doctor.html", ctx)

    def admin(self, request):
        from support.models import Ticket

        since = timezone.now() - timedelta(hours=24)
        ctx = {
            "open_tickets": Ticket.objects.filter(status__in=["open", "in_progress"]).count(),
            "unread_tickets": Ticket.objects.filter(admin_unread=True).count(),
            "n_doctors": User.objects.filter(role=Role.DOCTOR, is_active=True).count(),
            "n_pharm": User.objects.filter(role=Role.PHARMACIST, is_active=True).count(),
            "n_inactive": User.objects.filter(is_active=False).count(),
            "failed_24h": AuditLog.objects.filter(action=A.LOGIN_FAILED, timestamp__gte=since).count(),
            "lockouts_24h": AuditLog.objects.filter(action=A.LOCKOUT, timestamp__gte=since).count(),
            "denied_24h": AuditLog.objects.filter(action=A.DENIED, timestamp__gte=since).count(),
            "recent": AuditLog.objects.select_related("user")[:12],
        }
        return render(request, "clinic/dashboard_admin.html", ctx)


# ============================================================================ collection
class Collection(DoctorOnly, View):
    def get(self, request):
        td = timezone.localdate()
        month = td.replace(day=1)

        def parse(key, default):
            try:
                return datetime.strptime(request.GET[key], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                return default

        start, end = parse("from", month), parse("to", td)
        if start > end:
            start, end = end, start
        live = Visit.objects.exclude(status=Visit.Status.SAVED)
        bills = Bill.objects.filter(is_void=False)

        def tot(date_from, date_to):
            v, b = live.filter(date__range=(date_from, date_to)), bills.filter(date__range=(date_from, date_to))
            return _sum(v) + _sum(b)

        days = {}
        for src, key in ((live, "visits"), (bills, "bills")):
            for r in src.filter(date__range=(start, end)).values("date").annotate(s=Sum("total"), n=Count("id")):
                d = days.setdefault(r["date"], {"date": r["date"], "visits": Decimal("0"), "bills": Decimal("0"), "n": 0})
                d[key] += r["s"] or 0
                d["n"] += r["n"]
        rows = sorted(({**d, "total": d["visits"] + d["bills"]} for d in days.values()), key=lambda d: d["date"], reverse=True)
        methods = {}
        for src in (live, bills):
            for r in src.filter(date__range=(start, end)).values("payment_method").annotate(s=Sum("total")):
                methods[r["payment_method"]] = methods.get(r["payment_method"], Decimal("0")) + (r["s"] or 0)
        return render(request, "clinic/collection.html", {
            "start": start, "end": end, "rows": rows, "methods": sorted(methods.items()),
            "range_total": sum((r["total"] for r in rows), Decimal("0")),
            "today_total": tot(td, td), "month_total": tot(month, td),
            "yesterday": td - timedelta(days=1), "today": td, "month": month, "d30": td - timedelta(days=29),
        })


# ============================================================================ patients
class PatientList(DoctorOnly, View):
    def get(self, request):
        q = request.GET.get("q", "").strip()
        qs = Patient.objects.select_related("created_by")
        if q:
            cond = Q(name__icontains=q) | Q(phone__icontains=q)
            if q.isdigit():
                cond |= Q(reg_no=int(q))
            qs = qs.filter(cond)
        qs = qs.order_by("-reg_no")
        return render(request, "clinic/patient_list.html", {"page": _page(request, qs), "q": q})


class PatientCreate(DoctorOnly, View):
    def get(self, request):
        return render(request, "clinic/patient_form.html", {"form": PatientForm(), "creating": True})

    def post(self, request):
        form = PatientForm(request.POST)
        if form.is_valid():
            patient, _ = save_form(request, form)
            messages.success(request, f"Registered {patient.name} as #{patient.reg_no}.")
            if request.POST.get("then") == "visit":
                return redirect("clinic:visit_new", patient_pk=patient.pk)
            if request.POST.get("then") == "chronic":
                return redirect("clinic:chronic_new", patient_pk=patient.pk)
            return redirect("clinic:patient_detail", pk=patient.pk)
        return render(request, "clinic/patient_form.html", {"form": form, "creating": True})


class PatientEdit(DoctorOnly, View):
    def get(self, request, pk):
        p = get_object_or_404(Patient, pk=pk)
        return render(request, "clinic/patient_form.html", {"form": PatientForm(instance=p), "patient": p})

    def post(self, request, pk):
        with transaction.atomic():
            p = get_object_or_404(Patient.objects.select_for_update(), pk=pk)
            form = PatientForm(request.POST, instance=p)
            if form.is_valid():
                save_form(request, form)
                messages.success(request, "Patient details updated.")
                return redirect("clinic:patient_detail", pk=p.pk)
        return render(request, "clinic/patient_form.html", {"form": form, "patient": p})


class PatientDetail(DoctorOnly, View):
    def get(self, request, pk):
        p = get_object_or_404(Patient.objects.select_related("created_by", "updated_by"), pk=pk)
        log(request, A.VIEW, p)
        return render(request, "clinic/patient_detail.html", {
            "p": p,
            "visits": p.visits.select_related("created_by", "updated_by", "dispensed_by"),
            "appts": p.appointments.filter(date__gte=timezone.localdate()).exclude(status="cancelled"),
            "bills": p.bills.all()[:10],
            "chronic": p.chronic_cases.select_related("created_by")[:20],
            "trail": trail_for(p),
        })


class PatientHistoryPrint(DoctorOnly, View):
    """Complete, chronological case history (every visit with symptoms, investigations, medicines)."""

    def get(self, request, pk):
        p = get_object_or_404(Patient, pk=pk)

        def parse(key):
            try:
                return datetime.strptime(request.GET[key], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                return None

        start, end = parse("from"), parse("to")
        visits = p.visits.select_related("created_by").order_by("date", "created_at")
        if start:
            visits = visits.filter(date__gte=start)
        if end:
            visits = visits.filter(date__lte=end)
        log(request, A.PRINT, p, detail="Full history" + (f" {start or ''}..{end or ''}" if start or end else ""))
        return render(request, "clinic/print/history.html", {
            "p": p, "visits": visits, "start": start, "end": end,
            "show_fees": request.GET.get("fees") == "1", "show_inv": request.GET.get("inv", "1") == "1",
            "printed_on": timezone.localtime(), "autoprint": request.GET.get("auto") == "1",
        })


# ============================================================================ visits (case / Rx)
def _persist_visit(request, form, approve, appt=None):
    """Save a visit; on approve assign the daily token and stamp who approved. Call inside atomic()."""
    inst, now = form.instance, timezone.now()
    first_approval = approve and inst.status == Visit.Status.SAVED
    if first_approval:
        inst.status = Visit.Status.APPROVED
        inst.approved_by, inst.approved_at = request.user, now
        if not inst.token:
            inst.assign_token()
    elif inst.status == Visit.Status.APPROVED and inst.pk and form.has_changed():
        inst.revised_at = now  # tells the pharmacist the Rx changed after approval
    if appt:
        inst.appointment = appt
    visit, _ = save_form(request, form)
    if first_approval:
        log(request, A.APPROVE, visit, detail=f"Token #{visit.token} sent to pharmacy")
    if appt and appt.status != Appointment.Status.DONE:
        appt.status = Appointment.Status.DONE
        appt.updated_by = request.user
        appt.version += 1
        appt.save()
    return visit, first_approval


def _visit_post(request, form, approve, appt):
    if approve and not (form.cleaned_data.get("treatment") or "").strip():
        form.add_error("treatment", "Add treatment before sending to the pharmacy.")
        return None
    return _persist_visit(request, form, approve, appt)


def _after_visit(request, visit, approved):
    messages.success(request, "Approved — sent to the pharmacy queue." if approved else "Saved.")
    return redirect("clinic:visit_detail", pk=visit.pk) if approved else redirect("clinic:visit_edit", pk=visit.pk)


def _symptom_hints():
    seen, out = set(), []
    for s in Visit.objects.exclude(symptoms="").order_by("-id").values_list("symptoms", flat=True)[:300]:
        for line in (s or "").splitlines():
            t = line.strip().upper()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out[:300]


def _visit_ctx(form, visit, **extra):
    past = visit.patient.visits.select_related("created_by")
    if visit.pk:
        past = past.exclude(pk=visit.pk)
    past = past.order_by("-date", "-created_at")
    return {"form": form, "visit": visit, "patient": visit.patient, "symptom_hints": _symptom_hints(),
            "catalogue": CatalogItem.objects.filter(is_active=True),
            "history": past[:15], "history_total": past.count(),
            "chronic": visit.patient.chronic_cases.all()[:5], **extra}


class VisitCreate(DoctorOnly, View):
    def _appt(self, request, patient):
        aid = request.GET.get("appt") or request.POST.get("appt")
        return Appointment.objects.filter(pk=aid, patient=patient).first() if aid and aid.isdigit() else None

    def get(self, request, patient_pk):
        patient = get_object_or_404(Patient, pk=patient_pk)
        draft = Visit.objects.filter(patient=patient, date=timezone.localdate(), status=Visit.Status.SAVED).first()
        if draft:  # shared data: don't create a second draft for the same patient/day
            messages.info(request, f"A draft for today already exists (started by {draft.created_by or 'a colleague'}). Continuing it.")
            return redirect("clinic:visit_edit", pk=draft.pk)
        visit = Visit(patient=patient, visit_type=patient.patient_type)
        log(request, A.VIEW, patient, detail="Opened new visit (history panel)")
        return render(request, "clinic/visit_form.html", _visit_ctx(VisitForm(instance=visit), visit, appt=self._appt(request, patient)))

    def post(self, request, patient_pk):
        patient = get_object_or_404(Patient, pk=patient_pk)
        visit = Visit(patient=patient)
        form = VisitForm(request.POST, instance=visit)
        appt = self._appt(request, patient)
        if form.is_valid():
            with transaction.atomic():
                res = _visit_post(request, form, request.POST.get("action") == "approve", appt)
            if res:
                return _after_visit(request, *res)
        return render(request, "clinic/visit_form.html", _visit_ctx(form, visit, appt=appt))


class VisitEdit(DoctorOnly, View):
    def get(self, request, pk):
        visit = get_object_or_404(Visit.objects.select_related("patient"), pk=pk)
        if visit.is_locked:
            messages.info(request, "This prescription has been dispensed and is now read-only.")
            return redirect("clinic:visit_detail", pk=pk)
        log(request, A.VIEW, visit.patient, detail="Opened visit (history panel)")
        return render(request, "clinic/visit_form.html", _visit_ctx(VisitForm(instance=visit), visit))

    def post(self, request, pk):
        with transaction.atomic():
            visit = get_object_or_404(Visit.objects.select_for_update().select_related("patient"), pk=pk)
            if visit.is_locked:
                messages.error(request, "Already dispensed by the pharmacy — it can no longer be edited.")
                return redirect("clinic:visit_detail", pk=pk)
            form = VisitForm(request.POST, instance=visit)
            if form.is_valid():
                res = _visit_post(request, form, request.POST.get("action") == "approve", None)
                if res:
                    return _after_visit(request, *res)
        return render(request, "clinic/visit_form.html", _visit_ctx(form, visit))


class VisitDetail(DoctorOnly, View):
    def get(self, request, pk):
        v = get_object_or_404(Visit.objects.select_related("patient", "created_by", "updated_by", "approved_by", "dispensed_by"), pk=pk)
        log(request, A.VIEW, v)
        return render(request, "clinic/visit_detail.html", {"v": v, "p": v.patient, "trail": trail_for(v)})


class SentRx(DoctorOnly, View):
    def get(self, request):
        qs = Visit.objects.exclude(status=Visit.Status.SAVED).select_related("patient", "approved_by", "dispensed_by")
        st = request.GET.get("status")
        if st in ("approved", "dispensed"):
            qs = qs.filter(status=st)
        return render(request, "clinic/rx_list.html", {"page": _page(request, qs.order_by("-approved_at"), 30), "st": st})


# ============================================================================ chronic case taking
def _cc_prefill(patient):
    return {"allergies": (patient.allergies or "")[:300], "occupation": (patient.occupation or "")[:300],
            "diagnosis": (patient.diagnosis or "")[:300]}


def _cc_after(request, case, finish):
    if finish:
        messages.success(request, "Chronic case sheet saved.")
        return redirect("clinic:chronic_detail", pk=case.pk)
    messages.success(request, "Saved — you can continue filling the sheet.")
    return redirect("clinic:chronic_edit", pk=case.pk)


class ChronicCreate(DoctorOnly, View):
    def get(self, request, patient_pk):
        patient = get_object_or_404(Patient, pk=patient_pk)
        log(request, A.VIEW, patient, detail="Opened chronic case taking")
        form = ChronicCaseForm(instance=ChronicCase(patient=patient), prefill=_cc_prefill(patient))
        return render(request, "clinic/chronic_form.html", {"form": form, "patient": patient, "creating": True})

    def post(self, request, patient_pk):
        patient = get_object_or_404(Patient, pk=patient_pk)
        form = ChronicCaseForm(request.POST, instance=ChronicCase(patient=patient))
        if form.is_valid():
            case, _ = save_form(request, form)
            return _cc_after(request, case, request.POST.get("action") == "finish")
        return render(request, "clinic/chronic_form.html", {"form": form, "patient": patient, "creating": True})


class ChronicEdit(DoctorOnly, View):
    def get(self, request, pk):
        case = get_object_or_404(ChronicCase.objects.select_related("patient"), pk=pk)
        return render(request, "clinic/chronic_form.html", {"form": ChronicCaseForm(instance=case), "patient": case.patient, "case": case})

    def post(self, request, pk):
        with transaction.atomic():
            case = get_object_or_404(ChronicCase.objects.select_for_update().select_related("patient"), pk=pk)
            form = ChronicCaseForm(request.POST, instance=case)
            if form.is_valid():
                save_form(request, form)
                return _cc_after(request, case, request.POST.get("action") == "finish")
        return render(request, "clinic/chronic_form.html", {"form": form, "patient": case.patient, "case": case})


class ChronicDetail(DoctorOnly, View):
    def get(self, request, pk):
        case = get_object_or_404(ChronicCase.objects.select_related("patient", "created_by", "updated_by"), pk=pk)
        log(request, A.VIEW, case)
        return render(request, "clinic/chronic_detail.html", {"c": case, "p": case.patient, "sections": case.sections(), "trail": trail_for(case)})


class ChronicPrint(DoctorOnly, View):
    def get(self, request, pk):
        case = get_object_or_404(ChronicCase.objects.select_related("patient", "created_by"), pk=pk)
        log(request, A.PRINT, case)
        return render(request, "clinic/print/chronic.html", {"c": case, "p": case.patient, "sections": case.sections(),
                                                             "autoprint": request.GET.get("auto") == "1"})


# ============================================================================ prints & share
def _rx_receipt_ctx(v, quick):
    rows = []
    if v.consultation_fee:
        rows.append(("Consultation", 1, v.consultation_fee, v.consultation_fee))
    if v.medicine_fee:
        rows.append(("Medicine", 1, v.medicine_fee, v.medicine_fee))
    return {"label_no": "Reg No", "no": v.patient.reg_no, "date": v.date, "cust_label": "Patient", "name": v.patient.name,
            "phone": v.patient.phone, "rows": rows, "subtotal": v.consultation_fee + v.medicine_fee, "discount": v.discount,
            "total": v.total, "method": v.payment_method, "quick": quick}


class VisitPrint(DoctorOnly, View):
    def get(self, request, pk, kind):
        v = get_object_or_404(Visit.objects.select_related("patient"), pk=pk)
        log(request, A.PRINT, v, detail=kind)
        base = {"v": v, "p": v.patient, "autoprint": request.GET.get("auto") == "1"}
        if kind == "rx":
            return render(request, "clinic/print/rx.html", base)
        if kind == "investigation":
            return render(request, "clinic/print/investigation.html", base)
        if kind in ("receipt", "quick"):
            return render(request, "clinic/print/receipt.html", {**base, **_rx_receipt_ctx(v, kind == "quick")})
        raise Http404


def _wa_number(phone):
    n = re.sub(r"\D", "", phone or "")
    if len(n) == 10:
        n = settings.DEFAULT_COUNTRY_CODE + n
    return n


class VisitShare(DoctorOnly, View):
    """Opens WhatsApp with the text pre-filled. Audited because PHI leaves the system."""

    def get(self, request, pk, kind):
        if not settings.ENABLE_WHATSAPP_SHARE:
            raise Http404
        v = get_object_or_404(Visit.objects.select_related("patient"), pk=pk)
        num = _wa_number(v.patient.phone)
        if not num:
            messages.error(request, "This patient has no phone number.")
            return redirect("clinic:visit_detail", pk=pk)
        biz = ClinicSettings.load()
        if kind == "investigation":
            text = f"{biz.name}\nInvestigation request for {v.patient.name}:\n{v.investigations}"
        else:
            text = f"{biz.name}\n{v.patient.name}, {v.patient.age} yrs\nRx:\n{v.treatment}"
        log(request, A.SHARE, v, detail=f"WhatsApp {kind}")
        return HttpResponseRedirect(f"https://wa.me/{num}?text={quote(text)}")


# ============================================================================ pharmacy
class PharmacyQueue(PharmacistOnly, View):
    def get(self, request):
        q = Visit.objects.filter(status=Visit.Status.APPROVED).select_related("patient", "approved_by").order_by("approved_at")
        return render(request, "clinic/pharmacy_queue.html", {"queue": q, "tab": "queue"})


class PharmacyDone(PharmacistOnly, View):
    def get(self, request):
        days = {"1": 1, "7": 7, "30": 30}.get(request.GET.get("days", "1"), 1)
        since = timezone.localdate() - timedelta(days=days - 1)
        qs = Visit.objects.filter(status=Visit.Status.DISPENSED, dispensed_at__date__gte=since).select_related(
            "patient", "dispensed_by").order_by("-dispensed_at")
        return render(request, "clinic/pharmacy_done.html", {"page": _page(request, qs, 30), "tab": "done", "days": days})


class PharmacyDetail(PharmacistOnly, View):
    def get(self, request, pk):
        v = get_object_or_404(Visit.objects.select_related("patient", "approved_by", "dispensed_by"), pk=pk)
        if v.status == Visit.Status.SAVED:
            raise Http404
        log(request, A.VIEW, v, detail="Pharmacy view")
        return render(request, "clinic/pharmacy_detail.html", {"v": v, "p": v.patient, "trail": trail_for(v, 10)})


class PharmacyDispense(PharmacistOnly, View):
    def post(self, request, pk):
        with transaction.atomic():
            v = get_object_or_404(Visit.objects.select_for_update().select_related("patient", "dispensed_by"), pk=pk)
            if v.status == Visit.Status.DISPENSED:
                messages.warning(request, f"Already dispensed by {v.dispensed_by or 'a colleague'}.")
                return redirect("clinic:pharmacy_queue")
            if v.status != Visit.Status.APPROVED:
                raise Http404
            v.status = Visit.Status.DISPENSED
            v.dispensed_by, v.dispensed_at = request.user, timezone.now()
            v.pharmacy_note = request.POST.get("note", "").strip()[:200]
            v.updated_by = request.user
            v.version += 1
            v.save()
            log(request, A.DISPENSE, v, detail=f"Token #{v.token}")
        messages.success(request, f"Token #{v.token} · {v.patient.name} marked as dispensed.")
        return redirect("clinic:pharmacy_queue")


# ============================================================================ appointments
class AppointmentList(DoctorOnly, View):
    def _ctx(self, request, form):
        td = timezone.localdate()
        view = request.GET.get("view", "upcoming")
        qs = Appointment.objects.select_related("patient", "created_by")
        qs = qs.filter(date__lt=td).order_by("-date", "-time") if view == "past" else qs.filter(date__gte=td)
        return {"form": form, "appts": qs[:200], "view": view, "today": td}

    def get(self, request):
        initial = {"date": timezone.localdate()}
        if request.GET.get("patient", "").isdigit():
            initial["patient"] = request.GET["patient"]
        return render(request, "clinic/appointments.html", self._ctx(request, AppointmentForm(initial=initial)))

    def post(self, request):
        form = AppointmentForm(request.POST)
        if form.is_valid():
            save_form(request, form)
            messages.success(request, "Appointment booked.")
            return redirect("clinic:appointments")
        return render(request, "clinic/appointments.html", self._ctx(request, form))


class AppointmentStatus(DoctorOnly, View):
    def post(self, request, pk):
        a = get_object_or_404(Appointment, pk=pk)
        st = request.POST.get("status")
        if st not in Appointment.Status.values:
            raise Http404
        a.status, a.updated_by = st, request.user
        a.version += 1
        a.save()
        log(request, A.UPDATE, a, changed=["status"], detail=f"-> {st}")
        return redirect(request.POST.get("next") if request.POST.get("next") in ("/", "/appointments/") else "clinic:appointments")


# ============================================================================ billing
class BillList(ClinicalStaff, View):
    def get(self, request):
        q = request.GET.get("q", "").strip()
        qs = Bill.objects.select_related("created_by")
        if q:
            qs = qs.filter(Q(number__icontains=q) | Q(customer_name__icontains=q) | Q(phone__icontains=q))
        return render(request, "clinic/bill_list.html", {"page": _page(request, qs), "q": q})


class BillCreate(ClinicalStaff, View):
    def _ctx(self, form, formset):
        cat = CatalogItem.objects.filter(is_active=True)
        return {"form": form, "formset": formset, "next_no": ClinicSettings.load().next_bill_no, "catalogue": cat,
                "catalogue_json": [{"name": c.name, "price": str(c.price)} for c in cat]}

    def get(self, request):
        form = BillForm(initial={"date": timezone.localdate()})
        return render(request, "clinic/bill_form.html", self._ctx(form, BillItemFormSet(instance=Bill())))

    def post(self, request):
        bill = Bill()
        form, formset = BillForm(request.POST, instance=bill), BillItemFormSet(request.POST, instance=bill)
        if form.is_valid() and formset.is_valid():
            lines = [f.cleaned_data for f in formset.forms if f.cleaned_data.get("name")]
            sub = sum((c["qty"] * c["unit_price"] for c in lines), Decimal("0"))
            disc = form.cleaned_data["discount"]
            if disc > sub:
                form.add_error("discount", "Discount cannot exceed the subtotal.")
            else:
                with transaction.atomic():
                    biz = ClinicSettings.lock()
                    bill.number = str(biz.next_bill_no)
                    biz.next_bill_no += 1
                    biz.save(update_fields=["next_bill_no"])
                    bill.subtotal, bill.total = sub, sub - disc
                    p = form.cleaned_data.get("patient")
                    if p:
                        bill.customer_name = bill.customer_name or p.name
                        bill.phone = bill.phone or p.phone
                    save_form(request, form)
                    formset.instance = bill
                    formset.save()
                messages.success(request, f"Bill {bill.number} saved.")
                return redirect(f"{bill.get_absolute_url()}?auto=1")
        return render(request, "clinic/bill_form.html", self._ctx(form, formset))


class BillDetail(ClinicalStaff, View):
    def get(self, request, pk):
        b = get_object_or_404(Bill.objects.select_related("created_by", "void_by").prefetch_related("items"), pk=pk)
        log(request, A.PRINT if request.GET.get("auto") else A.VIEW, b)
        ctx = {"label_no": "Bill No", "no": b.number, "date": b.date, "cust_label": "Customer", "name": b.customer_name,
               "phone": b.phone, "rows": [(i.name, i.qty, i.unit_price, i.amount) for i in b.items.all()], "subtotal": b.subtotal,
               "discount": b.discount, "total": b.total, "method": b.payment_method, "quick": False, "bill": b,
               "autoprint": request.GET.get("auto") == "1" and not b.is_void, "void_form": VoidForm()}
        return render(request, "clinic/print/receipt.html", ctx)


class BillVoid(DoctorOnly, View):
    def post(self, request, pk):
        form = VoidForm(request.POST)
        with transaction.atomic():
            b = get_object_or_404(Bill.objects.select_for_update(), pk=pk)
            if b.is_void or not form.is_valid():
                messages.error(request, "Enter a reason to void this bill." if not b.is_void else "Bill is already void.")
            else:
                b.is_void, b.void_reason = True, form.cleaned_data["reason"]
                b.void_by, b.void_at, b.updated_by = request.user, timezone.now(), request.user
                b.version += 1
                b.save()
                log(request, A.VOID, b, detail=b.void_reason)
                messages.success(request, f"Bill {b.number} voided.")
        return redirect("clinic:bill_detail", pk=pk)


# ============================================================================ catalogue & settings
class Catalogue(CatalogueManagers, View):
    def _ctx(self, request, form, edit=None):
        q = request.GET.get("q", "").strip()
        qs = CatalogItem.objects.all()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
        return {"page": _page(request, qs, 40), "form": form, "edit": edit, "q": q, "import_form": CatalogImportForm()}

    def get(self, request):
        return render(request, "clinic/catalogue.html", self._ctx(request, CatalogForm()))

    def post(self, request):
        form = CatalogForm(request.POST)
        if form.is_valid():
            save_form(request, form)
            messages.success(request, "Item added.")
            return redirect("clinic:catalogue")
        return render(request, "clinic/catalogue.html", self._ctx(request, form))


class CatalogueEdit(CatalogueManagers, View):
    def get(self, request, pk):
        it = get_object_or_404(CatalogItem, pk=pk)
        return render(request, "clinic/catalogue.html", Catalogue()._ctx(request, CatalogForm(instance=it), it))

    def post(self, request, pk):
        it = get_object_or_404(CatalogItem, pk=pk)
        form = CatalogForm(request.POST, instance=it)
        if form.is_valid():
            save_form(request, form)
            messages.success(request, "Item updated.")
            return redirect("clinic:catalogue")
        return render(request, "clinic/catalogue.html", Catalogue()._ctx(request, form, it))


class CatalogueImport(CatalogueManagers, View):
    def post(self, request):
        form = CatalogImportForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
            return redirect("clinic:catalogue")
        try:
            rows = form.rows()
        except Exception as e:
            messages.error(request, getattr(e, "messages", [str(e)])[0])
            return redirect("clinic:catalogue")
        added = updated = 0
        with transaction.atomic():
            for code, name, price in rows:
                it = CatalogItem.objects.filter(name__iexact=name).first()
                if it:
                    it.code, it.price, it.updated_by, it.version = code or it.code, price, request.user, it.version + 1
                    it.save()
                    updated += 1
                else:
                    CatalogItem.objects.create(code=code, name=name, price=price, created_by=request.user, updated_by=request.user)
                    added += 1
        log(request, A.CREATE, detail=f"Catalogue CSV import: {added} added, {updated} updated")
        messages.success(request, f"Imported: {added} added, {updated} updated.")
        return redirect("clinic:catalogue")


class ClinicSettingsView(AdminOnly, View):
    def get(self, request):
        return render(request, "clinic/settings.html", {"form": SettingsForm(instance=ClinicSettings.load())})

    def post(self, request):
        form = SettingsForm(request.POST, instance=ClinicSettings.load())
        if form.is_valid():
            save_form(request, form)
            messages.success(request, "Clinic details saved.")
            return redirect("clinic:settings")
        return render(request, "clinic/settings.html", {"form": form})
