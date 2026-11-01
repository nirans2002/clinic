from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("audit/", views.AuditList.as_view(), name="audit"),
    path("audit/export/", views.AuditExport.as_view(), name="audit_export"),
]
