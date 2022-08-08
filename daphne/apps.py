# Import the server here to ensure the reactor is installed very early on in case other
# packages import twisted.internet.reactor (e.g. raven does this).
from django.apps import AppConfig
from django.core import checks

import daphne.server  # noqa: F401

from .checks import check_daphne_installed


class DaphneConfig(AppConfig):
    name = "daphne"
    verbose_name = "Daphne"

    def ready(self):
        checks.register(check_daphne_installed, checks.Tags.staticfiles)
