"""Minimal Django project copied into a temporary directory by runserver tests."""

import os
from pathlib import Path

from django.core.asgi import get_asgi_application
from django.http import JsonResponse
from django.urls import path

SECRET_KEY = "runserver-tests"
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "django.contrib.contenttypes"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
ROOT_URLCONF = "runserver_project"
ASGI_APPLICATION = "runserver_project.application"
STATIC_URL = "/static/"
STATICFILES_DIRS = [Path(__file__).parent / "static"]
MESSAGE = "first"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "django.utils.autoreload": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        }
    },
}


def view(request):
    return JsonResponse(
        {
            "message": MESSAGE,
            "secure": request.is_secure(),
            "scheme": request.scope["scheme"],
            "http_version": request.scope["http_version"],
            "pid": os.getpid(),
        }
    )


urlpatterns = [path("", view)]
django_application = None


async def application(scope, receive, send):
    """Custom ASGI middleware and WebSocket routing must survive runserver."""
    if scope["type"] == "websocket" and scope["path"] == "/ws/":
        assert (await receive())["type"] == "websocket.connect"
        await send({"type": "websocket.accept"})
        message = await receive()
        if message["type"] == "websocket.receive":
            await send({"type": "websocket.send", "text": message["text"]})
            await send({"type": "websocket.close"})
        return

    async def middleware_send(message):
        if message["type"] == "http.response.start":
            message.setdefault("headers", []).append(
                (b"x-asgi-middleware", b"configured")
            )
        await send(message)

    global django_application
    if django_application is None:
        django_application = get_asgi_application()
    await django_application(scope, receive, middleware_send)


if __name__ == "__main__":
    from django.core.management import execute_from_command_line

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "runserver_project")
    execute_from_command_line()
