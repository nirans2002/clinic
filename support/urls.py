from django.urls import path

from . import views

app_name = "support"
urlpatterns = [
    path("", views.TicketList.as_view(), name="list"),
    path("new/", views.TicketCreate.as_view(), name="new"),
    path("<int:pk>/", views.TicketDetail.as_view(), name="detail"),
    path("<int:pk>/close/", views.TicketClose.as_view(), name="close"),
]
