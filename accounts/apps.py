from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from . import checks  # noqa: F401  (registers system checks)
        from django.contrib.auth.signals import user_logged_in

        from .merge import on_user_logged_in

        user_logged_in.connect(
            on_user_logged_in, dispatch_uid="accounts.merge_session_on_login"
        )
