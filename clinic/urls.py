from django.urls import path

from . import views as v

app_name = "clinic"
urlpatterns = [
    path("", v.Dashboard.as_view(), name="dashboard"),
    path("collection/", v.Collection.as_view(), name="collection"),
    # patients
    path("patients/", v.PatientList.as_view(), name="patients"),
    path("patients/new/", v.PatientCreate.as_view(), name="patient_new"),
    path("patients/<int:pk>/", v.PatientDetail.as_view(), name="patient_detail"),
    path("patients/<int:pk>/history/", v.PatientHistoryPrint.as_view(), name="patient_history"),
    path("patients/<int:pk>/edit/", v.PatientEdit.as_view(), name="patient_edit"),
    # visits
    path("patients/<int:patient_pk>/visit/new/", v.VisitCreate.as_view(), name="visit_new"),
    path("visits/<int:pk>/", v.VisitDetail.as_view(), name="visit_detail"),
    path("visits/<int:pk>/edit/", v.VisitEdit.as_view(), name="visit_edit"),
    path("visits/<int:pk>/print/<slug:kind>/", v.VisitPrint.as_view(), name="visit_print"),
    path("visits/<int:pk>/share/<slug:kind>/", v.VisitShare.as_view(), name="visit_share"),
    path("patients/<int:patient_pk>/chronic/new/", v.ChronicCreate.as_view(), name="chronic_new"),
    path("chronic/<int:pk>/", v.ChronicDetail.as_view(), name="chronic_detail"),
    path("chronic/<int:pk>/edit/", v.ChronicEdit.as_view(), name="chronic_edit"),
    path("chronic/<int:pk>/print/", v.ChronicPrint.as_view(), name="chronic_print"),
    path("prescriptions/", v.SentRx.as_view(), name="sent_rx"),
    # pharmacy
    path("pharmacy/", v.PharmacyQueue.as_view(), name="pharmacy_queue"),
    path("pharmacy/dispensed/", v.PharmacyDone.as_view(), name="pharmacy_done"),
    path("pharmacy/<int:pk>/", v.PharmacyDetail.as_view(), name="pharmacy_detail"),
    path("pharmacy/<int:pk>/dispense/", v.PharmacyDispense.as_view(), name="pharmacy_dispense"),
    # appointments
    path("appointments/", v.AppointmentList.as_view(), name="appointments"),
    path("appointments/<int:pk>/status/", v.AppointmentStatus.as_view(), name="appointment_status"),
    # billing
    path("billing/", v.BillList.as_view(), name="bills"),
    path("billing/new/", v.BillCreate.as_view(), name="bill_new"),
    path("billing/<int:pk>/", v.BillDetail.as_view(), name="bill_detail"),
    path("billing/<int:pk>/void/", v.BillVoid.as_view(), name="bill_void"),
    path("catalogue/", v.Catalogue.as_view(), name="catalogue"),
    path("catalogue/import/", v.CatalogueImport.as_view(), name="catalogue_import"),
    path("catalogue/<int:pk>/", v.CatalogueEdit.as_view(), name="catalogue_edit"),
    # admin master
    path("manage/settings/", v.ClinicSettingsView.as_view(), name="settings"),
]
