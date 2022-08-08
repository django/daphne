# Django system check to ensure daphne app is listed in INSTALLED_APPS before django.contrib.staticfiles.
from django.core.checks import Error, register


@register()
def check_daphne_installed(app_configs, **kwargs):
    from django.apps import apps
    from django.contrib.staticfiles.apps import StaticFilesConfig

    from daphne.apps import DaphneConfig

    for app in apps.get_app_configs():
        if isinstance(app, DaphneConfig):
            return []
        if isinstance(app, StaticFilesConfig):
            return [
                Error(
                    "Daphne must be listed before django.contrib.staticfiles in INSTALLED_APPS.",
                    id="daphne.E001",
                )
            ]
