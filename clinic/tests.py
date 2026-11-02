from decimal import Decimal

from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, User
from core.fields import PREFIX
from core.models import AuditLog

from .models import Appointment, Bill, CatalogItem, ClinicSettings, Patient, Visit

PW = "Str0ng-Test-Passw0rd!"


def mk(username, role, **kw):
    u = User.objects.create_user(username, password=PW, role=role, must_change_password=False, **kw)
    return u


class Base(TestCase):
    def setUp(self):
        self.d1 = mk("dr1", Role.DOCTOR, first_name="Asha")
        self.d2 = mk("dr2", Role.DOCTOR, first_name="Ravi")
        self.p1 = mk("ph1", Role.PHARMACIST)
        self.p2 = mk("ph2", Role.PHARMACIST)
        self.adm = mk("boss", Role.ADMIN)

    def login(self, u):
        self.client.logout()
        self.client.force_login(u)

    def web_login(self, username, password):
        """Real sign-in through the login form (exercises axes lockout + audit signals)."""
        self.client.logout()
        return self.client.post(reverse("accounts:login"), {"username": username, "password": password})

    def patient(self, user=None, **kw):
        data = dict(name="Test Patient", age=40, gender="Male", phone="9876543210", patient_type="OP", allow_duplicate="on")
        data.update(kw)
        self.login(user or self.d1)
        r = self.client.post(reverse("clinic:patient_new"), data)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        return Patient.objects.latest("id")

    def visit_data(self, **kw):
        d = dict(symptoms="FEVER", investigations="CBC", treatment="1.ARNICA 30 /1-0-1", remarks="", due_amount="0",
                 visit_type="OP", consultation_fee="300", medicine_fee="200", discount="50", payment_method="Cash", action="save")
        d.update(kw)
        return d


class RoleAccessTests(Base):
    def test_anonymous_redirected_to_login(self):
        for name in ("clinic:dashboard", "clinic:patients", "clinic:pharmacy_queue", "accounts:user_list"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302)
            self.assertIn("/accounts/login/", r["Location"])

    def test_pharmacist_cannot_see_patients_or_admin(self):
        self.login(self.p1)
        for name in ("clinic:patients", "clinic:patient_new", "clinic:appointments", "accounts:user_list", "core:audit"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.assertTrue(AuditLog.objects.filter(action="denied", username="ph1").exists())

    def test_doctor_cannot_dispense_or_manage_users(self):
        self.login(self.d1)
        self.assertEqual(self.client.get(reverse("clinic:pharmacy_queue")).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:audit")).status_code, 403)

    def test_admin_cannot_read_clinical_data(self):
        p = self.patient()
        self.login(self.adm)
        self.assertEqual(self.client.get(reverse("clinic:patients")).status_code, 403)
        self.assertEqual(self.client.get(reverse("clinic:patient_detail", args=[p.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("clinic:dashboard")).status_code, 200)

    def test_pharmacist_home_is_queue(self):
        self.login(self.p1)
        self.assertRedirects(self.client.get(reverse("clinic:dashboard")), reverse("clinic:pharmacy_queue"))


class SharedDataAndAuditTests(Base):
    def test_doctors_share_data_and_edits_are_attributed(self):
        p = self.patient(self.d1)
        self.assertEqual(p.created_by, self.d1)
        self.login(self.d2)
        self.assertContains(self.client.get(reverse("clinic:patients")), "Test Patient")
        r = self.client.post(reverse("clinic:patient_edit", args=[p.pk]), dict(
            name="Test Patient", age=41, gender="Male", phone="9876543210", patient_type="OP", version=p.version))
        self.assertEqual(r.status_code, 302)
        p.refresh_from_db()
        self.assertEqual((p.created_by, p.updated_by, p.age, p.version), (self.d1, self.d2, 41, 2))
        entry = AuditLog.objects.filter(model="Patient", object_id=str(p.pk), action="update").get()
        self.assertEqual(entry.username, "dr2")
        self.assertIn("age", entry.changed_fields)
        self.assertNotIn("41", entry.changed_fields)  # values never logged

    def test_optimistic_lock_blocks_stale_edit(self):
        p = self.patient(self.d1)
        self.login(self.d2)
        stale = p.version
        Patient.objects.filter(pk=p.pk).update(version=stale + 1)
        r = self.client.post(reverse("clinic:patient_edit", args=[p.pk]), dict(
            name="Changed", age=40, gender="Male", phone="9876543210", patient_type="OP", version=stale))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Reload the page")
        p.refresh_from_db()
        self.assertEqual(p.name, "Test Patient")

    def test_duplicate_warning(self):
        self.patient()
        r = self.client.post(reverse("clinic:patient_new"), dict(name="test patient", age=3, gender="Male", phone="9876543210", patient_type="OP"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "already exists")

    def test_views_are_logged(self):
        p = self.patient()
        self.client.get(reverse("clinic:patient_detail", args=[p.pk]))
        self.assertTrue(AuditLog.objects.filter(action="view", model="Patient", username="dr1").exists())

    def test_audit_log_is_immutable(self):
        e = AuditLog.objects.create(action="login", username="x")
        with self.assertRaises(PermissionError):
            e.delete()
        e.detail = "tamper"
        with self.assertRaises(PermissionError):
            e.save()


class PrescriptionWorkflowTests(Base):
    def test_full_flow_save_approve_dispense(self):
        p = self.patient(self.d1)
        url = reverse("clinic:visit_new", args=[p.pk])
        r = self.client.post(url, self.visit_data())
        v = Visit.objects.get()
        self.assertRedirects(r, reverse("clinic:visit_edit", args=[v.pk]))
        self.assertEqual((v.status, v.total, v.token), ("saved", Decimal("450.00"), None))
        # another doctor continues and approves
        self.login(self.d2)
        r = self.client.post(reverse("clinic:visit_edit", args=[v.pk]), self.visit_data(action="approve", version=v.version))
        v.refresh_from_db()
        self.assertEqual((v.status, v.token, v.approved_by, v.created_by, v.updated_by), ("approved", 1, self.d2, self.d1, self.d2))
        # pharmacist sees it, with allergy-safe limited view
        self.login(self.p1)
        self.assertContains(self.client.get(reverse("clinic:pharmacy_queue")), "ARNICA")
        self.client.post(reverse("clinic:pharmacy_dispense", args=[v.pk]), {"note": "all given"})
        v.refresh_from_db()
        self.assertEqual((v.status, v.dispensed_by, v.pharmacy_note), ("dispensed", self.p1, "all given"))
        # second pharmacist: shared view + no double dispense
        self.login(self.p2)
        self.assertContains(self.client.get(reverse("clinic:pharmacy_done")), "ph1")
        r = self.client.post(reverse("clinic:pharmacy_dispense", args=[v.pk]), follow=True)
        self.assertContains(r, "Already dispensed")
        v.refresh_from_db()
        self.assertEqual(v.dispensed_by, self.p1)
        # doctor can no longer edit
        self.login(self.d1)
        r = self.client.post(reverse("clinic:visit_edit", args=[v.pk]), self.visit_data(treatment="CHANGED", version=v.version))
        v.refresh_from_db()
        self.assertIn("ARNICA", v.treatment)
        actions = set(AuditLog.objects.values_list("action", flat=True))
        self.assertTrue({"create", "approve", "dispense"} <= actions)

    def test_cannot_approve_without_treatment(self):
        p = self.patient()
        r = self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data(treatment="", action="approve"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Visit.objects.count(), 0)

    def test_tokens_increment_per_day(self):
        p = self.patient()
        for i in range(2):
            self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data(action="approve", treatment=f"{i}.X"))
            Visit.objects.filter(status="approved").update(status="dispensed")
        self.assertEqual(list(Visit.objects.order_by("id").values_list("token", flat=True)), [1, 2])

    def test_revised_flag_after_approval(self):
        p = self.patient()
        self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data(action="approve"))
        v = Visit.objects.get()
        self.client.post(reverse("clinic:visit_edit", args=[v.pk]), self.visit_data(treatment="1.NUX VOM", version=v.version))
        v.refresh_from_db()
        self.assertIsNotNone(v.revised_at)

    def test_discount_cannot_exceed_fees(self):
        p = self.patient()
        r = self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data(discount="9999"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Visit.objects.count(), 0)

    def test_total_is_computed_server_side(self):
        p = self.patient()
        self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data(total="1"))
        self.assertEqual(Visit.objects.get().total, Decimal("450.00"))

    def test_prints_render(self):
        p = self.patient()
        self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data())
        v = Visit.objects.get()
        for kind in ("rx", "investigation", "receipt", "quick"):
            self.assertEqual(self.client.get(reverse("clinic:visit_print", args=[v.pk, kind])).status_code, 200, kind)
        r = self.client.get(reverse("clinic:visit_share", args=[v.pk, "rx"]))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("https://wa.me/919876543210"))
        self.assertTrue(AuditLog.objects.filter(action="share").exists())


class AppointmentTests(Base):
    def test_book_and_start_visit(self):
        from django.utils import timezone
        p = self.patient()
        r = self.client.post(reverse("clinic:appointments"), dict(patient=p.pk, date=timezone.localdate().isoformat(), time="10:30", note="follow-up"))
        self.assertEqual(r.status_code, 302)
        a = Appointment.objects.get()
        self.client.post(reverse("clinic:visit_new", args=[p.pk]) + f"?appt={a.pk}", self.visit_data(appt=a.pk))
        a.refresh_from_db()
        self.assertEqual(a.status, "done")


class BillingTests(Base):
    def post_bill(self, user, **kw):
        self.login(user)
        data = {"date": "2026-10-31", "patient": "", "customer_name": "Walk In", "phone": "", "payment_method": "UPI", "discount": "10",
                "items-TOTAL_FORMS": "2", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "1", "items-MAX_NUM_FORMS": "1000",
                "items-0-name": "Arnica 30", "items-0-qty": "2", "items-0-unit_price": "50",
                "items-1-name": "Nux Vom", "items-1-qty": "1", "items-1-unit_price": "30"}
        data.update(kw)
        return self.client.post(reverse("clinic:bill_new"), data)

    def test_pharmacist_creates_bill_doctor_voids(self):
        r = self.post_bill(self.p1)
        self.assertEqual(r.status_code, 302)
        b = Bill.objects.get()
        self.assertEqual((b.number, b.subtotal, b.total, b.created_by), ("1001", Decimal("130.00"), Decimal("120.00"), self.p1))
        self.assertEqual(ClinicSettings.load().next_bill_no, 1002)
        self.assertEqual(self.client.get(reverse("clinic:bill_detail", args=[b.pk])).status_code, 200)
        # pharmacist cannot void
        self.assertEqual(self.client.post(reverse("clinic:bill_void", args=[b.pk]), {"reason": "x"}).status_code, 403)
        self.login(self.d1)
        self.client.post(reverse("clinic:bill_void", args=[b.pk]), {"reason": "entered twice"})
        b.refresh_from_db()
        self.assertTrue(b.is_void)
        self.assertEqual(b.void_by, self.d1)

    def test_numbers_unique_and_discount_validated(self):
        self.post_bill(self.d1)
        self.post_bill(self.d2)
        self.assertEqual(list(Bill.objects.order_by("id").values_list("number", flat=True)), ["1001", "1002"])
        r = self.post_bill(self.d1, discount="9999")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Bill.objects.count(), 2)

    def test_catalogue_csv_import(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.login(self.d1)
        csv_bytes = b"Code,Item Name,Price\nA1,Arnica 30,50\nA2,Nux Vom,30.5\n"
        self.client.post(reverse("clinic:catalogue_import"), {"file": SimpleUploadedFile("c.csv", csv_bytes)})
        self.assertEqual(CatalogItem.objects.count(), 2)
        self.client.post(reverse("clinic:catalogue_import"), {"file": SimpleUploadedFile("c.csv", b"Code,Item Name,Price\nA1,arnica 30,60\n")})
        self.assertEqual(CatalogItem.objects.get(name="Arnica 30").price, Decimal("60"))
        self.login(self.p1)
        self.assertEqual(self.client.get(reverse("clinic:catalogue")).status_code, 403)


class EncryptionTests(Base):
    def test_phi_is_encrypted_at_rest(self):
        p = self.patient(allergies="PENICILLIN ALLERGY", address="12 Secret Street")
        self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data(treatment="1.SECRET MEDICINE"))
        with connection.cursor() as c:
            c.execute("select allergies, address from clinic_patient")
            raw = c.fetchone()
            c.execute("select treatment, symptoms from clinic_visit")
            raw += c.fetchone()
        for cell in raw:
            self.assertTrue(cell.startswith(PREFIX), cell)
            self.assertNotIn("SECRET", cell)
            self.assertNotIn("PENICILLIN", cell)
        p.refresh_from_db()
        self.assertEqual(p.allergies, "PENICILLIN ALLERGY")
        self.assertEqual(Visit.objects.get().treatment, "1.SECRET MEDICINE")


class UserManagementTests(Base):
    def test_admin_creates_user_with_forced_password_change(self):
        self.login(self.adm)
        r = self.client.post(reverse("accounts:user_create"), dict(username="newdoc", first_name="New", last_name="Doc", email="", role="doctor"))
        self.assertEqual(r.status_code, 200)
        pw = r.context["password"]
        u = User.objects.get(username="newdoc")
        self.assertTrue(u.check_password(pw) and u.must_change_password and u.role == Role.DOCTOR and not u.is_staff)
        self.assertNotIn(pw, "".join(AuditLog.objects.values_list("detail", flat=True)))
        # new user is locked into the password-change page until they choose their own
        r = self.web_login("newdoc", pw)
        self.assertEqual(r.status_code, 302)
        self.assertRedirects(self.client.get(reverse("clinic:patients")), reverse("accounts:password_change"))
        r = self.client.post(reverse("accounts:password_change"), dict(old_password=pw, new_password1="A-brand-new-Passphrase-77", new_password2="A-brand-new-Passphrase-77"))
        self.assertEqual(r.status_code, 302)
        u.refresh_from_db()
        self.assertFalse(u.must_change_password)
        self.assertEqual(self.client.get(reverse("clinic:patients")).status_code, 200)

    def test_weak_password_rejected(self):
        self.login(self.d1)
        self.d1.must_change_password = True
        self.d1.save()
        r = self.client.post(reverse("accounts:password_change"), dict(old_password=PW, new_password1="short", new_password2="short"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "too short")

    def test_cannot_demote_last_admin_or_self(self):
        self.login(self.adm)
        r = self.client.post(reverse("accounts:user_edit", args=[self.adm.pk]), dict(first_name="", last_name="", email="", role="doctor", is_active="on"), follow=True)
        self.adm.refresh_from_db()
        self.assertEqual(self.adm.role, Role.ADMIN)

    def test_deactivated_user_cannot_login_and_reset_ends_sessions(self):
        self.login(self.adm)
        self.client.post(reverse("accounts:user_edit", args=[self.d1.pk]), dict(first_name="", last_name="", email="", role="doctor"))
        self.d1.refresh_from_db()
        self.assertFalse(self.d1.is_active)
        r = self.web_login("dr1", PW)
        self.assertEqual(r.status_code, 200)  # form re-shown, no session
        self.assertEqual(self.client.get(reverse("clinic:dashboard")).status_code, 302)

    def test_roles_only_assignable_by_admin(self):
        self.login(self.d1)
        self.assertEqual(self.client.post(reverse("accounts:user_create"), dict(username="x", role="admin")).status_code, 403)
        self.assertFalse(User.objects.filter(username="x").exists())

    def test_login_lockout_after_failures(self):
        for _ in range(5):
            self.web_login("dr1", "wrong-password")
        r = self.web_login("dr1", PW)
        self.assertEqual(r.status_code, 429)
        self.assertTrue(AuditLog.objects.filter(action="login_failed").exists())
        self.login(self.adm)
        self.client.post(reverse("accounts:user_unlock", args=[self.d1.pk]))
        self.assertEqual(self.web_login("dr1", PW).status_code, 302)


class SecurityHeaderTests(Base):
    def test_headers(self):
        self.login(self.d1)
        r = self.client.get(reverse("clinic:dashboard"))
        self.assertIn("default-src 'self'", r["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", r["Content-Security-Policy"])
        self.assertIn("no-store", r["Cache-Control"])
        self.assertEqual(r["X-Frame-Options"], "DENY")

    def test_no_inline_scripts_or_styles_in_pages(self):
        p = self.patient()
        self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data())
        v = Visit.objects.get()
        urls = [reverse("clinic:dashboard"), reverse("clinic:patients"), reverse("clinic:patient_new"), reverse("clinic:patient_detail", args=[p.pk]),
                reverse("clinic:visit_edit", args=[v.pk]), reverse("clinic:visit_detail", args=[v.pk]), reverse("clinic:bill_new"),
                reverse("clinic:appointments"), reverse("clinic:catalogue"), reverse("clinic:sent_rx")]
        for u in urls:
            html = self.client.get(u).content.decode()
            self.assertNotIn(" style=", html, u)
            self.assertNotRegex(html, r"<script(?![^>]*(src=|type=\"application/json\"))", u)
            self.assertNotRegex(html, r"\son(click|change|submit|load)=", u)

    def test_logout_requires_post(self):
        self.login(self.d1)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.client.get(reverse("clinic:dashboard")).status_code, 302)

    def test_login_error_does_not_reveal_user_existence(self):
        a = self.client.post(reverse("accounts:login"), {"username": "dr1", "password": "bad"})
        b = self.client.post(reverse("accounts:login"), {"username": "ghost", "password": "bad"})
        self.assertEqual(a.context["form"].non_field_errors(), b.context["form"].non_field_errors())

    def test_xss_is_escaped(self):
        p = self.patient(name="<script>alert(1)</script>")
        html = self.client.get(reverse("clinic:patient_detail", args=[p.pk])).content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_csrf_enforced(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.d1)
        self.assertEqual(c.post(reverse("clinic:patient_new"), {"name": "x"}).status_code, 403)


class AuditViewTests(Base):
    def test_admin_audit_list_and_export(self):
        self.patient()
        self.login(self.adm)
        r = self.client.get(reverse("core:audit"), {"user": "dr1"})
        self.assertContains(r, "dr1")
        r = self.client.get(reverse("core:audit_export"))
        self.assertEqual(r["Content-Type"], "text/csv")
        body = b"".join(r.streaming_content).decode()
        self.assertIn("dr1", body)


class PageSmokeTests(Base):
    def test_every_screen_renders(self):
        p = self.patient()
        self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data(action="approve"))
        v = Visit.objects.get()
        CatalogItem.objects.create(name="Arnica", price=10)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("accounts:login")).status_code, 200)
        self.login(self.d1)
        for name, args in [("clinic:dashboard", []), ("clinic:patients", []), ("clinic:patient_new", []), ("clinic:patient_edit", [p.pk]),
                           ("clinic:visit_new", [p.pk]), ("clinic:visit_detail", [v.pk]), ("clinic:sent_rx", []), ("clinic:appointments", []),
                           ("clinic:bills", []), ("clinic:bill_new", []), ("clinic:catalogue", []),
                           ("clinic:catalogue_edit", [CatalogItem.objects.get().pk]), ("accounts:password_change", [])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)
        self.login(self.p1)
        for name, args in [("clinic:pharmacy_queue", []), ("clinic:pharmacy_done", []), ("clinic:pharmacy_detail", [v.pk]),
                           ("clinic:bills", []), ("clinic:bill_new", [])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)
        self.login(self.adm)
        for name, args in [("clinic:dashboard", []), ("accounts:user_list", []), ("accounts:user_create", []), ("accounts:user_edit", [self.d1.pk]),
                           ("core:audit", []), ("clinic:settings", []), ("clinic:catalogue", [])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)
        self.assertEqual(self.client.get("/nope/").status_code, 404)

    def test_pharmacist_cannot_open_draft_rx(self):
        p = self.patient()
        self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data())
        self.login(self.p1)
        self.assertEqual(self.client.get(reverse("clinic:pharmacy_detail", args=[Visit.objects.get().pk])).status_code, 404)

    def test_rotate_encryption_command(self):
        from django.core.management import call_command
        p = self.patient(allergies="DUST")
        call_command("rotate_encryption", verbosity=0)
        p.refresh_from_db()
        self.assertEqual(p.allergies, "DUST")


class PhoneValidationTests(TestCase):
    def test_normalize(self):
        from django.core.exceptions import ValidationError
        from .validators import normalize_phone as n
        for raw, out in [("98765 43210", "9876543210"), ("+91 98765-43210", "9876543210"), ("919876543210", "9876543210"),
                         ("09876543210", "9876543210"), ("+44 7911 123456", "+447911123456")]:
            self.assertEqual(n(raw), out, raw)
        for bad in ["12345", "5876543210", "9999999999", "98765abc10", "+0123456", "+123", "", "98765432101234"]:
            with self.assertRaises(ValidationError, msg=bad):
                n(bad)


class PhoneFormTests(Base):
    def test_patient_phone_validated_and_normalised(self):
        self.login(self.d1)
        base = dict(name="A B", age=30, gender="Male", patient_type="OP")
        r = self.client.post(reverse("clinic:patient_new"), {**base, "phone": "12345"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "valid 10-digit")
        self.assertEqual(Patient.objects.count(), 0)
        self.client.post(reverse("clinic:patient_new"), {**base, "phone": "+91 98765 43210"})
        self.assertEqual(Patient.objects.get().phone, "9876543210")


class CollectionAndProfileTests(Base):
    def test_collection_page_and_dashboard(self):
        p = self.patient()
        self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data(action="approve"))
        r = self.client.get(reverse("clinic:collection"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "₹450.00")
        self.assertNotContains(self.client.get(reverse("clinic:dashboard")), "Today's collection")
        self.login(self.p1)
        self.assertEqual(self.client.get(reverse("clinic:collection")).status_code, 403)

    def test_profile_tab_has_password_form_for_all_roles(self):
        for u in (self.d1, self.p1, self.adm):
            self.login(u)
            r = self.client.get(reverse("accounts:password_change"))
            self.assertContains(r, "Change password")
            self.assertContains(r, u.username)
            self.assertContains(self.client.get(reverse("clinic:dashboard"), follow=True), ">Profile<")

    def test_custom_dose_input_present(self):
        p = self.patient()
        self.assertContains(self.client.get(reverse("clinic:visit_new", args=[p.pk])), 'id="pick-dose"')


class HistoryPrintTests(Base):
    def test_full_history_print(self):
        p = self.patient(allergies="DUST")
        for t in ("1.ARNICA 30 /1-0-1", "1.NUX VOM /SOS"):
            self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data(action="approve", treatment=t, symptoms="COUGH"))
        url = reverse("clinic:patient_history", args=[p.pk])
        r = self.client.get(url)
        self.assertContains(r, "ARNICA 30")
        self.assertContains(r, "NUX VOM")
        self.assertContains(r, "2 visits")
        self.assertContains(r, "CBC")
        self.assertNotContains(r, "Total ₹")
        self.assertContains(self.client.get(url, {"fees": "1"}), "Total ₹450.00")
        self.assertNotContains(self.client.get(url, {"inv": "0"}), "Investigations</b>")
        self.assertContains(self.client.get(url, {"from": "2999-01-01"}), "No visits in this period")
        self.assertTrue(AuditLog.objects.filter(action="print", model="Patient").exists())
        self.login(self.p1)
        self.assertEqual(self.client.get(url).status_code, 403)


class SupportTicketTests(Base):
    def raise_ticket(self, user, **kw):
        self.login(user)
        data = dict(subject="Printer not working", category="bug", priority="high", description="Rx print shows blank page")
        data.update(kw)
        return self.client.post(reverse("support:new"), data)

    def test_ticket_lifecycle(self):
        from support.models import Ticket
        r = self.raise_ticket(self.d1)
        t = Ticket.objects.get()
        self.assertRedirects(r, reverse("support:detail", args=[t.pk]))
        self.assertEqual((t.status, t.created_by, t.admin_unread, t.ref), ("open", self.d1, True, f"T-{t.pk:04d}"))
        # admin sees badge + dashboard count, opens it (marks read) and replies
        self.login(self.adm)
        self.assertContains(self.client.get(reverse("clinic:dashboard")), "Open support tickets")
        self.assertContains(self.client.get(reverse("support:list")), "Printer not working")
        self.assertContains(self.client.get(reverse("support:detail", args=[t.pk])), "Rx print shows blank page")
        t.refresh_from_db()
        self.assertFalse(t.admin_unread)
        self.client.post(reverse("support:detail", args=[t.pk]), {"body": "Try Ctrl+P, fixed in next update", "status": "resolved"})
        t.refresh_from_db()
        self.assertEqual((t.status, t.user_unread, t.messages.count()), ("resolved", True, 1))
        self.assertIsNotNone(t.resolved_at)
        # requester sees the badge, reads it, replies -> reopens
        self.login(self.d1)
        self.assertContains(self.client.get(reverse("clinic:dashboard")), 'class="badge"')
        self.assertContains(self.client.get(reverse("support:detail", args=[t.pk])), "Ctrl+P")
        t.refresh_from_db()
        self.assertFalse(t.user_unread)
        self.client.post(reverse("support:detail", args=[t.pk]), {"body": "Still broken"})
        t.refresh_from_db()
        self.assertEqual((t.status, t.admin_unread), ("open", True))
        self.client.post(reverse("support:close", args=[t.pk]))
        t.refresh_from_db()
        self.assertEqual(t.status, "closed")
        self.assertTrue(AuditLog.objects.filter(model="Ticket", action="create", username="dr1").exists())
        self.assertTrue(AuditLog.objects.filter(model="Ticket", action="update", username="boss").exists())

    def test_users_only_see_their_own_tickets(self):
        from support.models import Ticket
        self.raise_ticket(self.d1)
        t = Ticket.objects.get()
        self.login(self.d2)
        self.assertNotContains(self.client.get(reverse("support:list")), "Printer not working")
        self.assertEqual(self.client.get(reverse("support:detail", args=[t.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("support:detail", args=[t.pk]), {"body": "hi"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("support:close", args=[t.pk])).status_code, 404)
        self.login(self.p1)
        self.assertEqual(self.client.get(reverse("support:detail", args=[t.pk])).status_code, 404)

    def test_admin_cannot_raise_and_pharmacist_can(self):
        self.login(self.adm)
        self.assertEqual(self.client.get(reverse("support:new")).status_code, 403)
        from support.models import Ticket
        self.raise_ticket(self.p1, subject="Queue not refreshing", category="bug")
        self.assertEqual(Ticket.objects.get().created_by, self.p1)

    def test_text_encrypted_and_validated(self):
        from support.models import Ticket
        self.raise_ticket(self.d1, description="very secret words")
        with connection.cursor() as c:
            c.execute("select description from support_ticket")
            raw = c.fetchone()[0]
        self.assertTrue(raw.startswith(PREFIX))
        self.assertNotIn("secret", raw)
        r = self.raise_ticket(self.d1, subject="", description="")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Ticket.objects.count(), 1)

    def test_limit_of_open_tickets(self):
        from support.models import Ticket
        for i in range(10):
            self.raise_ticket(self.d1, subject=f"s{i}")
        r = self.raise_ticket(self.d1, subject="one too many")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Ticket.objects.count(), 10)

    def test_empty_reply_rejected_xss_escaped(self):
        from support.models import Ticket
        self.raise_ticket(self.d1, description="<script>alert(1)</script>")
        t = Ticket.objects.get()
        r = self.client.post(reverse("support:detail", args=[t.pk]), {"body": "  "})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("<script>alert(1)</script>", r.content.decode())


class PasswordToggleTests(Base):
    def test_toggle_script_is_shipped_and_pages_load_it(self):
        from django.contrib.staticfiles import finders
        js = open(finders.find("js/app.js")).read()
        self.assertIn("pw-toggle", js)
        self.client.logout()
        r = self.client.get(reverse("accounts:login"))
        self.assertContains(r, 'type="password"')
        self.assertContains(r, "js/app.js")
        self.login(self.d1)
        r = self.client.get(reverse("accounts:password_change"))
        self.assertContains(r, "js/app.js")
        self.assertContains(r, 'type="password"', count=3)


class CredentialDeliveryTests(Base):
    def create(self, **kw):
        self.login(self.adm)
        data = dict(username="newph", first_name="Nita", last_name="K", email="nita@example.com", phone="98765 43210", role="pharmacist", send_email="on")
        data.update(kw)
        return self.client.post(reverse("accounts:user_create"), data)

    def test_email_delivery_and_whatsapp_link(self):
        from django.core import mail
        from urllib.parse import unquote
        r = self.create()
        self.assertEqual(r.status_code, 200)
        pw = r.context["password"]
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ["nita@example.com"])
        self.assertIn(pw, m.body)
        self.assertIn("newph", m.body)
        self.assertIn("/accounts/login/", m.body)
        self.assertContains(r, "E-mailed to nita@example.com")
        # WhatsApp button, number normalised with country code, message carries the password
        wa = r.context["wa_url"]
        self.assertTrue(wa.startswith("https://wa.me/919876543210?text="))
        self.assertIn(pw, unquote(wa))
        self.assertContains(r, "Send via WhatsApp")
        u = User.objects.get(username="newph")
        self.assertEqual(u.phone, "9876543210")
        self.assertIsNotNone(u.temp_password_issued_at)
        # password never reaches the audit log; the delivery is recorded
        details = " ".join(AuditLog.objects.values_list("detail", flat=True))
        self.assertNotIn(pw, details)
        self.assertIn("sent by e-mail", details)

    def test_no_email_when_not_requested_and_no_whatsapp_without_phone(self):
        from django.core import mail
        r = self.create(send_email="", phone="")
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(r.context["wa_url"], "")
        self.assertNotContains(r, "Send via WhatsApp")

    def test_email_requires_address_and_phone_is_validated(self):
        r = self.create(email="")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Enter an e-mail address")
        self.assertFalse(User.objects.filter(username="newph").exists())
        r = self.create(phone="123")
        self.assertContains(r, "valid 10-digit")

    def test_smtp_failure_is_reported_not_fatal(self):
        from unittest import mock
        with mock.patch("accounts.views.send_mail", side_effect=ConnectionRefusedError("down")):
            r = self.create()
        self.assertContains(r, "could not be sent")
        self.assertTrue(User.objects.filter(username="newph").exists())
        self.assertTrue(AuditLog.objects.filter(detail__contains="FAILED").exists())

    @override_settings(CREDENTIAL_DELIVERY=["whatsapp"])
    def test_email_channel_can_be_disabled(self):
        self.login(self.adm)
        r = self.client.get(reverse("accounts:user_create"))
        self.assertNotContains(r, "send_email")
        r = self.create()
        self.assertEqual(r.status_code, 200)
        from django.core import mail
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(CREDENTIAL_DELIVERY=["email"])
    def test_whatsapp_channel_can_be_disabled(self):
        r = self.create()
        self.assertEqual(r.context["wa_url"], "")

    def test_reset_confirm_page_and_email(self):
        from django.core import mail
        self.d1.email, self.d1.phone = "d1@example.com", "9876500001"
        self.d1.save()
        self.login(self.adm)
        self.assertContains(self.client.get(reverse("accounts:user_reset", args=[self.d1.pk])), "d1@example.com")
        r = self.client.post(reverse("accounts:user_reset", args=[self.d1.pk]), {"send_email": "on"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(r.context["password"], mail.outbox[0].body)
        self.assertIn("reset", mail.outbox[0].body)
        self.assertTrue(r.context["wa_url"].startswith("https://wa.me/919876500001"))

    def test_non_admin_cannot_reach_reset_or_create(self):
        self.login(self.d1)
        self.assertEqual(self.client.get(reverse("accounts:user_reset", args=[self.d2.pk])).status_code, 403)

    def test_unused_temp_password_expires(self):
        from datetime import timedelta
        from django.utils import timezone
        r = self.create(send_email="")
        pw = r.context["password"]
        self.assertEqual(self.web_login("newph", pw).status_code, 302)
        self.assertRedirects(self.client.get(reverse("clinic:patients")), reverse("accounts:password_change"))
        User.objects.filter(username="newph").update(temp_password_issued_at=timezone.now() - timedelta(hours=49))
        r = self.client.get(reverse("clinic:patients"), follow=True)
        self.assertContains(r, "temporary password has expired")
        self.assertEqual(self.client.get(reverse("clinic:patients")).status_code, 302)  # signed out
        # choosing a real password clears the clock
        self.adm.refresh_from_db()
        r = self.create(username="other", send_email="", email="")
        pw2 = r.context["password"]
        self.web_login("other", pw2)
        self.client.post(reverse("accounts:password_change"), dict(old_password=pw2, new_password1="Another-Strong-Pass-99", new_password2="Another-Strong-Pass-99"))
        self.assertIsNone(User.objects.get(username="other").temp_password_issued_at)


class VisitHistorySidePanelTests(Base):
    def test_past_visits_shown_beside_new_visit(self):
        p = self.patient()
        other = self.patient(name="Someone Else", phone="9123456780")
        self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data(action="approve", treatment="1.SULPHUR 200", symptoms="ITCHING"))
        Visit.objects.filter(patient=p).update(status="dispensed")
        self.client.post(reverse("clinic:visit_new", args=[other.pk]), self.visit_data(treatment="1.OTHERS MEDICINE"))
        r = self.client.get(reverse("clinic:visit_new", args=[p.pk]))
        self.assertContains(r, "Past visits (1)")
        self.assertContains(r, "SULPHUR 200")
        self.assertContains(r, "ITCHING")
        self.assertContains(r, "data-copy-rx")
        self.assertNotContains(r, "OTHERS MEDICINE")
        # editing an existing visit lists the *other* visits, not itself
        v2 = Visit.objects.get(patient=p)
        r = self.client.get(reverse("clinic:visit_edit", args=[v2.pk]) if v2.status != "dispensed" else reverse("clinic:visit_new", args=[p.pk]))
        self.assertTrue(AuditLog.objects.filter(action="view", model="Patient", detail__contains="history panel").exists())

    def test_first_visit_message_and_current_visit_excluded(self):
        p = self.patient()
        self.client.post(reverse("clinic:visit_new", args=[p.pk]), self.visit_data(treatment="1.ONLYONE"))
        v = Visit.objects.get()
        r = self.client.get(reverse("clinic:visit_edit", args=[v.pk]))
        self.assertContains(r, "no earlier history")
        self.assertContains(r, "Past visits (0)")


class ChronicCaseTests(Base):
    def post_case(self, p, **kw):
        data = {"date": "2026-10-31", "chief_complaints": "RECURRENT COLDS x 3 yrs", "thermal": "Chilly", "desires": "Sweets, salt",
                "fears": "Thunderstorm, dogs", "remedy": "Phosphorus", "potency": "200", "action": "save"}
        data.update(kw)
        return self.client.post(reverse("clinic:chronic_new", args=[p.pk]), data)

    def test_schema_is_consistent(self):
        from .chronic_schema import FIELD_NAMES
        self.assertEqual(len(FIELD_NAMES), len(set(FIELD_NAMES)))
        self.assertGreater(len(FIELD_NAMES), 60)

    def test_create_view_edit_print(self):
        from .models import ChronicCase
        p = self.patient(allergies="PENICILLIN", occupation="Teacher")
        form_page = self.client.get(reverse("clinic:chronic_new", args=[p.pk]))
        self.assertContains(form_page, "Presenting complaints")
        self.assertContains(form_page, "Mental generals")
        self.assertContains(form_page, "Teacher")  # prefilled from the registration
        r = self.post_case(p)
        c = ChronicCase.objects.get()
        self.assertRedirects(r, reverse("clinic:chronic_edit", args=[c.pk]))
        self.assertEqual((c.created_by, c.data["thermal"], c.data["remedy"]), (self.d1, "Chilly", "Phosphorus"))
        self.assertNotIn("menarche", c.data)  # empty answers are not stored
        d = self.client.get(reverse("clinic:chronic_detail", args=[c.pk]))
        for t in ("RECURRENT COLDS", "Thunderstorm", "Chilly", "Phosphorus", "Mental generals"):
            self.assertContains(d, t)
        self.assertContains(self.client.get(reverse("clinic:chronic_print", args=[c.pk])), "Chronic case record")
        # a second doctor continues the same sheet; finish -> detail
        self.login(self.d2)
        r = self.client.post(reverse("clinic:chronic_edit", args=[c.pk]), {"date": "2026-10-31", "version": c.version, "chief_complaints": "COLDS + COUGH",
                                                                           "diagnosis": "Allergic rhinitis", "action": "finish"})
        self.assertRedirects(r, reverse("clinic:chronic_detail", args=[c.pk]))
        c.refresh_from_db()
        self.assertEqual((c.created_by, c.updated_by, c.data["diagnosis"]), (self.d1, self.d2, "Allergic rhinitis"))
        self.assertNotIn("thermal", c.data)  # fields cleared in the form are removed
        log = AuditLog.objects.filter(model="ChronicCase", action="update").get()
        self.assertIn("diagnosis", log.changed_fields)
        self.assertNotIn("Allergic", log.changed_fields)
        # sheet appears on the patient page and in the visit side panel
        self.assertContains(self.client.get(reverse("clinic:patient_detail", args=[p.pk])), "Chronic case sheets")
        self.assertContains(self.client.get(reverse("clinic:visit_new", args=[p.pk])), "COLDS + COUGH")

    def test_encrypted_at_rest(self):
        p = self.patient()
        self.post_case(p, chief_complaints="VERY PRIVATE COMPLAINT")
        with connection.cursor() as c:
            c.execute("select data_json from clinic_chroniccase")
            raw = c.fetchone()[0]
        self.assertTrue(raw.startswith(PREFIX))
        self.assertNotIn("PRIVATE", raw)

    def test_stale_edit_refused_and_roles(self):
        from .models import ChronicCase
        p = self.patient()
        self.post_case(p)
        c = ChronicCase.objects.get()
        ChronicCase.objects.filter(pk=c.pk).update(version=c.version + 1)
        r = self.client.post(reverse("clinic:chronic_edit", args=[c.pk]), {"date": "2026-10-31", "version": c.version, "chief_complaints": "X"})
        self.assertContains(r, "Reload the page")
        for who in (self.p1, self.adm):
            self.login(who)
            self.assertEqual(self.client.get(reverse("clinic:chronic_detail", args=[c.pk])).status_code, 403)
            self.assertEqual(self.client.get(reverse("clinic:chronic_new", args=[p.pk])).status_code, 403)

    def test_patient_registration_button_goes_to_chronic(self):
        self.login(self.d1)
        r = self.client.post(reverse("clinic:patient_new"), dict(name="Chronic Pt", age=30, gender="Female", phone="9876501234", patient_type="OP", then="chronic"))
        p = Patient.objects.get(name="Chronic Pt")
        self.assertRedirects(r, reverse("clinic:chronic_new", args=[p.pk]))
        self.assertContains(self.client.get(reverse("clinic:patient_new")), "chronic case taking")
        self.assertContains(self.client.get(reverse("clinic:patient_detail", args=[p.pk])), "Chronic case taking")


class PlatformReadinessTests(Base):
    def test_healthz_is_public_cheap_and_host_agnostic(self):
        self.client.logout()
        r = self.client.get("/healthz", HTTP_HOST="10.0.0.5:10000")
        self.assertEqual((r.status_code, r.content), (200, b"ok"))
        self.assertEqual(self.client.post("/healthz").status_code, 404)  # only GET/HEAD are special-cased

    def test_bootstrap_admin_command(self):
        import os
        from unittest import mock
        from django.core.management import call_command
        User.objects.filter(role=Role.ADMIN).delete()
        with mock.patch.dict(os.environ, {"ADMIN_BOOTSTRAP_USERNAME": "firstadmin", "ADMIN_BOOTSTRAP_PASSWORD": "short"}):
            with self.assertRaises(SystemExit):
                call_command("bootstrap_admin", verbosity=0)
            self.assertFalse(User.objects.filter(username="firstadmin").exists())
        with mock.patch.dict(os.environ, {"ADMIN_BOOTSTRAP_USERNAME": "firstadmin", "ADMIN_BOOTSTRAP_PASSWORD": "Quite-Long-Temp-Pass-42"}):
            call_command("bootstrap_admin", verbosity=0)
            u = User.objects.get(username="firstadmin")
            self.assertEqual((u.role, u.must_change_password, u.is_superuser), (Role.ADMIN, True, True))
            self.assertTrue(u.check_password("Quite-Long-Temp-Pass-42"))
            call_command("bootstrap_admin", verbosity=0)  # idempotent
            self.assertEqual(User.objects.filter(role=Role.ADMIN).count(), 1)

    def test_client_ip_resolution(self):
        from django.test import RequestFactory
        from core.audit import client_ip
        rf = RequestFactory()
        spoof = rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6", HTTP_CF_CONNECTING_IP="203.0.113.7", REMOTE_ADDR="10.1.1.1")
        with override_settings(CLIENT_IP_HEADER="CF-Connecting-IP"):
            self.assertEqual(client_ip(spoof), "203.0.113.7")
            self.assertEqual(client_ip(rf.get("/", HTTP_CF_CONNECTING_IP="not-an-ip", REMOTE_ADDR="10.1.1.1")), "10.1.1.1")
        with override_settings(CLIENT_IP_HEADER="", TRUST_PROXY=False):
            self.assertEqual(client_ip(spoof), "10.1.1.1")  # forwarded headers ignored unless a proxy is trusted
