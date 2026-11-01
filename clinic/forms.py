import csv
import io
import re
from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from core.forms import VersionedModelForm

from .validators import normalize_phone

from .chronic_schema import SECTIONS, fields as chronic_fields
from .models import Appointment, Bill, BillItem, CatalogItem, ChronicCase, ClinicSettings, Patient, Visit



def money(label, required=False):
    return forms.DecimalField(label=label, required=required, min_value=0, max_digits=10, decimal_places=2,
                              widget=forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal"}))


class PatientForm(VersionedModelForm):
    email = forms.EmailField(label="E-mail", required=False)
    address = forms.CharField(required=False, widget=forms.TextInput)
    diagnosis = forms.CharField(required=False, widget=forms.TextInput)
    allow_duplicate = forms.BooleanField(required=False, label="Register anyway (this is a different person)")

    class Meta:
        model = Patient
        fields = ["name", "age", "dob", "gender", "phone", "place", "address", "email", "patient_type", "diagnosis",
                  "blood_group", "referred_by", "occupation", "allergies", "is_active"]
        widgets = {"dob": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "allergies": forms.Textarea(attrs={"rows": 3}),
                   "age": forms.NumberInput(attrs={"min": 0, "max": 120}),
                   "phone": forms.TextInput(attrs={"type": "tel", "inputmode": "tel", "maxlength": 18, "placeholder": "10-digit mobile"})}

    duplicate_found = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["blood_group"].choices = [("", "-- Select --")] + Patient.BLOOD
        if not self.instance.pk:
            self.fields.pop("is_active")

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

    def clean_age(self):
        a = self.cleaned_data["age"]
        if a > 120:
            raise forms.ValidationError("Enter a valid age.")
        return a

    def clean_phone(self):
        return normalize_phone(self.cleaned_data["phone"])

    def clean(self):
        data = super().clean()
        if not self.instance.pk and data.get("name") and data.get("phone") and not data.get("allow_duplicate"):
            dup = Patient.objects.filter(name__iexact=data["name"], phone=data["phone"]).first()
            if dup:
                self.duplicate_found = True
                raise forms.ValidationError(f"A patient with the same name and phone already exists (#{dup.reg_no}). "
                                            "Open that record, or tick “Register anyway”.")
        return data


class VisitForm(VersionedModelForm):
    consultation_fee = money("Consultation")
    medicine_fee = money("Medicine")
    discount = money("Discount")
    due_amount = money("Due amount")

    class Meta:
        model = Visit
        fields = ["symptoms", "investigations", "treatment", "remarks", "due_amount", "visit_type",
                  "consultation_fee", "medicine_fee", "discount", "payment_method"]
        widgets = {"remarks": forms.TextInput, "visit_type": forms.RadioSelect,
                   "symptoms": forms.Textarea(attrs={"rows": 10}), "investigations": forms.Textarea(attrs={"rows": 10}),
                   "treatment": forms.Textarea(attrs={"rows": 10})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["visit_type"].choices = [("OP", "OP"), ("IP", "IP"), ("ONLINE", "ONLINE")]

    def _zero(self, name):
        return self.cleaned_data.get(name) or Decimal("0")

    clean_consultation_fee = lambda self: self._zero("consultation_fee")
    clean_medicine_fee = lambda self: self._zero("medicine_fee")
    clean_discount = lambda self: self._zero("discount")
    clean_due_amount = lambda self: self._zero("due_amount")

    def clean(self):
        d = super().clean()
        if d.get("discount") is not None and d["discount"] > (d.get("consultation_fee") or 0) + (d.get("medicine_fee") or 0):
            self.add_error("discount", "Discount cannot exceed consultation + medicine.")
        return d


class AppointmentForm(forms.ModelForm):
    patient = forms.ModelChoiceField(queryset=Patient.objects.filter(is_active=True).order_by("name"))

    class Meta:
        model = Appointment
        fields = ["patient", "date", "time", "note"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
                   "note": forms.TextInput}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["patient"].label_from_instance = lambda p: f"#{p.reg_no} · {p.name} · {p.phone}"

    note = forms.CharField(required=False, widget=forms.TextInput)

    def clean_date(self):
        from django.utils import timezone
        d = self.cleaned_data["date"]
        if d < timezone.localdate():
            raise forms.ValidationError("Choose today or a future date.")
        return d


class BillForm(forms.ModelForm):
    discount = money("Discount")

    class Meta:
        model = Bill
        fields = ["date", "patient", "customer_name", "phone", "payment_method", "discount"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "phone": forms.TextInput(attrs={"type": "tel", "inputmode": "tel", "maxlength": 18})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["patient"].queryset = Patient.objects.filter(is_active=True).order_by("name")
        self.fields["patient"].required = False
        self.fields["patient"].empty_label = "Walk-in / type name"
        self.fields["patient"].label_from_instance = lambda p: f"#{p.reg_no} · {p.name}"

    def clean_discount(self):
        return self.cleaned_data.get("discount") or Decimal("0")

    def clean_phone(self):
        p = self.cleaned_data.get("phone", "").strip()
        return normalize_phone(p) if p else ""


class BillItemForm(forms.ModelForm):
    class Meta:
        model = BillItem
        fields = ["name", "qty", "unit_price"]
        widgets = {"name": forms.TextInput(attrs={"list": "catalogue-list", "autocomplete": "off"}),
                   "qty": forms.NumberInput(attrs={"min": 1}),
                   "unit_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"})}


BillItemFormSet = inlineformset_factory(Bill, BillItem, form=BillItemForm, extra=3, can_delete=False, min_num=1, validate_min=True)


class VoidForm(forms.Form):
    reason = forms.CharField(max_length=200, widget=forms.TextInput(attrs={"placeholder": "Reason for voiding"}))


class CatalogForm(forms.ModelForm):
    class Meta:
        model = CatalogItem
        fields = ["code", "name", "price", "is_active"]

    def clean_name(self):
        n = " ".join(self.cleaned_data["name"].split())
        qs = CatalogItem.objects.filter(name__iexact=n)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("An item with this name already exists.")
        return n


class CatalogImportForm(forms.Form):
    file = forms.FileField(label="CSV file (columns: Code, Item Name, Price)")

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > 1024 * 1024:
            raise forms.ValidationError("File too large (max 1 MB).")
        if not f.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload a .csv file.")
        return f

    def rows(self):
        text = self.cleaned_data["file"].read().decode("utf-8-sig", errors="replace")
        data = list(csv.reader(io.StringIO(text)))
        if not data:
            raise forms.ValidationError("The file is empty.")
        head = [h.strip().lower() for h in data[0]]
        try:
            ni = next(i for i, h in enumerate(head) if "name" in h or "item" in h)
            pi = next(i for i, h in enumerate(head) if "price" in h)
        except StopIteration:
            raise forms.ValidationError("Need “Item Name” and “Price” columns.")
        ci = next((i for i, h in enumerate(head) if "code" in h), None)
        out = []
        for r in data[1:2001]:
            if len(r) <= max(ni, pi) or not r[ni].strip():
                continue
            try:
                price = Decimal(r[pi].strip() or "0")
            except Exception:
                continue
            if price < 0:
                continue
            out.append(((r[ci].strip() if ci is not None and ci < len(r) else "")[:30], " ".join(r[ni].split())[:150], price))
        return out


class SettingsForm(forms.ModelForm):
    class Meta:
        model = ClinicSettings
        fields = ["name", "doctor_line", "phone", "address", "gstin", "next_bill_no"]


class ChronicCaseForm(VersionedModelForm):
    class Meta:
        model = ChronicCase
        fields = ["date"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, prefill=None, **kwargs):
        super().__init__(*args, **kwargs)
        saved = self.instance.data if self.instance.pk else (prefill or {})
        for name, label, kind, choices in chronic_fields():
            if kind == "choice":
                f = forms.ChoiceField(label=label, required=False, choices=[("", "—")] + [(c, c) for c in choices])
            elif kind == "area":
                f = forms.CharField(label=label, required=False, max_length=3000, widget=forms.Textarea(attrs={"rows": 3}))
            else:
                f = forms.CharField(label=label, required=False, max_length=300)
            f.initial = saved.get(name, "")
            self.fields[name] = f

    def grouped(self):
        out = []
        for key, title, flds in SECTIONS:
            bound = [self[f[0]] for f in flds]
            out.append((key, title, bound, any(b.value() for b in bound)))
        return out

    def save(self, commit=True):
        self.instance.data = {n: (self.cleaned_data.get(n) or "").strip() for n in
                              (f[0] for f in chronic_fields()) if (self.cleaned_data.get(n) or "").strip()}
        return super().save(commit=commit)
