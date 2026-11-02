from django.conf import settings
from django.contrib import admin
from django.urls import include, path

handler403 = "core.views.permission_denied"
handler404 = "core.views.not_found"
handler500 = "core.views.server_error"

urlpatterns = [
    path("accounts/", include("accounts.urls")),
    path("manage/", include("core.urls")),
    path("support/", include("support.urls")),
    path("", include("clinic.urls")),
]

if settings.ENABLE_DJANGO_ADMIN:
    urlpatterns.insert(0, path(settings.ADMIN_URL, admin.site.urls))
