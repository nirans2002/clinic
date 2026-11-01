from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.Login.as_view(), name="login"),
    path("logout/", views.Logout.as_view(), name="logout"),
    path("profile/", views.ChangePassword.as_view(), name="password_change"),
    path("users/", views.UserList.as_view(), name="user_list"),
    path("users/new/", views.UserCreate.as_view(), name="user_create"),
    path("users/<int:pk>/", views.UserEdit.as_view(), name="user_edit"),
    path("users/<int:pk>/reset/", views.UserResetPassword.as_view(), name="user_reset"),
    path("users/<int:pk>/unlock/", views.UserUnlock.as_view(), name="user_unlock"),
]
