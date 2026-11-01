import json
from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.db.models import Max, Q
from django.db.models.functions import Lower
from django.utils import timezone

from core.fields import EncryptedTextField
from core.models import AuditedModel

ZERO = Decimal("0.00")
PAY_METHODS = [("Cash", "Cash"), ("UPI", "UPI"), ("Card", "Card")]
VISIT_TYPES = [("OP", "OP"), ("IP", "IP"), ("ONLINE", "ONLINE")]


class ClinicSettings(AuditedModel):
    """Single row: letterhead details + the sequence lock for bill numbers / tokens."""

    name = models.CharField(max_length=150, default="Dr. Nipun's Homoeopathy")
    doctor_line = models.CharField("Doctor name / qualification", max_length=150, blank=True, default="Dr. Nipun")
    phone = models.CharField(max_length=40, blank=True)
    address = models.CharField(max_length=250, blank=True)
    gstin = models.CharField("GSTIN (optional)", max_length=20, blank=True)
    next_bill_no = models.PositiveIntegerField(default=1001)

    class Meta:
        verbose_name_plural = "Clinic settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def lock(cls):
        """Row-lock used to serialise token / registration / bill-number allocation."""
        cls.objects.get_or_create(pk=1)
        return cls.objects.select_for_update().get(pk=1)

    def __str__(self):
        return self.name


class CatalogItem(AuditedModel):
    code = models.CharField(max_length=30, blank=True)
    name = models.CharField(max_length=150)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="uniq_catalog_name_ci")]

    def __str__(self):
        return self.name


class Patient(AuditedModel):
    GENDERS = [("Male", "Male"), ("Female", "Female"), ("Other", "Other")]
    BLOOD = [(b, b) for b in ["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"]]

    reg_no = models.PositiveIntegerField(unique=True, editable=False)
    name = models.CharField(max_length=120, db_index=True)
    age = models.PositiveSmallIntegerField()
    dob = models.DateField("Date of birth", null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDERS)
    phone = models.CharField(max_length=20, db_index=True)
    place = models.CharField(max_length=80, blank=True)
    address = EncryptedTextField(blank=True)
    email = EncryptedTextField(blank=True)
    patient_type = models.CharField("Type", max_length=8, choices=VISIT_TYPES, default="OP")
    diagnosis = EncryptedTextField(blank=True)
    blood_group = models.CharField(max_length=4, choices=BLOOD, blank=True)
    referred_by = models.CharField(max_length=100, blank=True)
    occupation = models.CharField(max_length=100, blank=True)
    allergies = EncryptedTextField("Allergic info / other details", blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["reg_no"]

    def save(self, *args, **kwargs):
        if not self.reg_no:
            with transaction.atomic():
                ClinicSettings.lock()
                top = Patient.objects.aggregate(m=Max("reg_no"))["m"] or 0
                self.reg_no = max(top + 1, settings.FIRST_REG_NO)
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"#{self.reg_no} {self.name}"


class Visit(AuditedModel):
    class Status(models.TextChoices):
        SAVED = "saved", "Saved (draft)"
        APPROVED = "approved", "Sent to pharmacy"
        DISPENSED = "dispensed", "Dispensed"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="visits")
    appointment = models.ForeignKey("Appointment", null=True, blank=True, on_delete=models.SET_NULL, related_name="visits")
    date = models.DateField(default=timezone.localdate, db_index=True)
    token = models.PositiveIntegerField(null=True, blank=True, editable=False)
    visit_type = models.CharField(max_length=8, choices=VISIT_TYPES, default="OP")
    symptoms = EncryptedTextField(blank=True)
    investigations = EncryptedTextField(blank=True)
    treatment = EncryptedTextField(blank=True)
    remarks = EncryptedTextField("Remarks | Invoice No", blank=True)
    due_amount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    consultation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    medicine_fee = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO, editable=False)
    payment_method = models.CharField(max_length=8, choices=PAY_METHODS, default="Cash")

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SAVED, db_index=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    revised_at = models.DateTimeField(null=True, blank=True)
    dispensed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    dispensed_at = models.DateTimeField(null=True, blank=True)
    pharmacy_note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["date", "token"], condition=Q(token__isnull=False), name="uniq_token_per_day"),
        ]

    def save(self, *args, **kwargs):
        self.total = max(ZERO, (self.consultation_fee or ZERO) + (self.medicine_fee or ZERO) - (self.discount or ZERO))
        super().save(*args, **kwargs)

    @property
    def is_locked(self):
        return self.status == self.Status.DISPENSED

    @property
    def treatment_lines(self):
        return [ln.strip() for ln in (self.treatment or "").splitlines() if ln.strip()]

    def assign_token(self):
        """Call inside transaction.atomic(); gives the next daily token number."""
        ClinicSettings.lock()
        top = Visit.objects.filter(date=self.date).aggregate(m=Max("token"))["m"] or 0
        self.token = top + 1

    def __str__(self):
        return f"Visit {self.date:%d-%m-%Y} · {self.patient.name}"


class Appointment(AuditedModel):
    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"
        ARRIVED = "arrived", "Arrived"
        DONE = "done", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="appointments")
    date = models.DateField(db_index=True)
    time = models.TimeField(null=True, blank=True)
    note = EncryptedTextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.BOOKED)

    class Meta:
        ordering = ["date", "time"]

    def __str__(self):
        return f"Appt {self.date} {self.patient.name}"


class Bill(AuditedModel):
    number = models.CharField(max_length=20, unique=True, editable=False)
    date = models.DateField(default=timezone.localdate)
    patient = models.ForeignKey(Patient, null=True, blank=True, on_delete=models.SET_NULL, related_name="bills")
    customer_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO, editable=False)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO, editable=False)
    payment_method = models.CharField(max_length=8, choices=PAY_METHODS, default="Cash")
    is_void = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    void_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    void_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("clinic:bill_detail", args=[self.pk])

    def __str__(self):
        return f"Bill {self.number}"


class BillItem(models.Model):
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=150)
    qty = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def amount(self):
        return self.qty * self.unit_price


class ChronicCase(AuditedModel):
    """Classical chronic case-taking sheet. The whole sheet is one encrypted JSON document."""

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="chronic_cases")
    date = models.DateField(default=timezone.localdate, db_index=True)
    data_json = EncryptedTextField(default="{}", blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    @property
    def data(self):
        try:
            d = json.loads(self.data_json or "{}")
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}

    @data.setter
    def data(self, value):
        self.data_json = json.dumps(value, ensure_ascii=False)

    def sections(self):
        """[(title, [(label, value), ...])] containing only the answered fields – for display and print."""
        from .chronic_schema import SECTIONS

        d, out = self.data, []
        for key, title, flds in SECTIONS:
            rows = [(f[1], d[f[0]]) for f in flds if d.get(f[0])]
            if rows:
                out.append((title, rows))
        return out

    @property
    def complaint_summary(self):
        return (self.data.get("chief_complaints") or "").strip()

    def __str__(self):
        return f"Chronic case {self.date:%d-%m-%Y} · {self.patient.name}"
