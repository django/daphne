import django
from django.conf import settings
from django.test.utils import override_settings

from daphne.checks import check_daphne_installed


def test_check_daphne_installed():
    """
    Test check error is raised if daphne is not listed before staticfiles, and vice versa.
    """
    settings.configure(
        INSTALLED_APPS=["daphne.apps.DaphneConfig", "django.contrib.staticfiles"]
    )
    django.setup()
    errors = check_daphne_installed(None)
    assert len(errors) == 0
    with override_settings(INSTALLED_APPS=["django.contrib.staticfiles", "daphne"]):
        errors = check_daphne_installed(None)
        assert len(errors) == 1
        assert errors[0].id == "daphne.E001"
