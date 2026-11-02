from io import StringIO
from unittest.mock import Mock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.management import CommandError, call_command
from django.test.utils import override_settings
from twisted.internet.endpoints import serverFromString
from twisted.internet.testing import MemoryReactor

from daphne.management.commands.runserver import Command, RunserverCommand


async def application(scope, receive, send):
    pass


@pytest.fixture
def command(django_setup):
    with override_settings(
        DEBUG=True,
        SETTINGS_MODULE="test_settings",
        ASGI_APPLICATION="test_runserver.application",
        STATIC_URL="/static/",
    ):
        command = Command(stdout=StringIO())
        command.check = Mock()
        command.check_migrations = Mock()
        command.server_cls = Mock()
        yield command


def invoke(command, *args, **kwargs):
    # Exercise the real parser, handle(), and Django's run() dispatch, without
    # starting a reactor or the reloader in this process.
    kwargs.setdefault("use_reloader", False)
    call_command(command, *args, **kwargs)
    return command.server_cls.call_args.kwargs


def test_http_defaults(command):
    kwargs = invoke(command)
    assert kwargs["endpoints"] == ["tcp:port=8000:interface=127.0.0.1"]
    assert "http://127.0.0.1:8000/" in command.stdout.getvalue()
    assert kwargs["signal_handlers"] is True
    assert isinstance(kwargs["application"], ASGIStaticFilesHandler)
    assert kwargs["application"].application is application
    command.check.assert_called_once_with(display_num_errors=True)
    command.check_migrations.assert_called_once_with()


@pytest.mark.parametrize("reloader", [False, True])
@pytest.mark.parametrize(
    "addrport, host, url",
    [
        ("8123", "127.0.0.1", "127.0.0.1:8123"),
        ("0.0.0.0:8123", "0.0.0.0", "0.0.0.0:8123"),
        ("[::1]:8123", "::1", "[::1]:8123"),
    ],
)
def test_tls(command, certificates, reloader, addrport, host, url):
    certfile, keyfile = certificates
    with patch("django.utils.autoreload.run_with_reloader") as reload:
        reload.side_effect = lambda callback, **options: callback(**options)
        kwargs = invoke(
            command,
            addrport,
            "--certfile",
            str(certfile),
            "--keyfile",
            str(keyfile),
            use_reloader=reloader,
        )
    assert reload.called is reloader
    assert kwargs["signal_handlers"] is not reloader
    assert len(kwargs["endpoints"]) == 1
    reactor = MemoryReactor()
    endpoint = serverFromString(reactor, kwargs["endpoints"][0])
    endpoint.listen(Mock())
    port, _, _, _, interface = reactor.sslServers[0]
    assert (port, interface) == (8123, host)
    assert "https://%s/" % url in command.stdout.getvalue()
    assert kwargs["application"].application is application
    command.check.assert_called_once()
    command.check_migrations.assert_called_once()


@pytest.mark.parametrize("flag", ["--certfile", "--keyfile"])
def test_incomplete_tls(command, flag):
    with pytest.raises(CommandError, match="must be supplied together"):
        invoke(command, flag, "missing.pem")
    command.server_cls.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        ["--certfile", "cert.pem"],
        ["--keyfile", "key.pem"],
        ["--certfile", "cert.pem", "--keyfile", "key.pem"],
    ],
)
def test_noasgi_rejects_tls(command, args):
    with patch.object(RunserverCommand, "handle") as handle:
        with pytest.raises(CommandError, match="cannot be used with --noasgi"):
            invoke(command, "--noasgi", *args)
    handle.assert_not_called()


def test_noasgi_still_dispatches_to_django(command):
    with patch.object(RunserverCommand, "inner_run") as inner_run:
        call_command(command, "--noasgi", "--noreload")
    inner_run.assert_called_once()
    assert command.server_cls is RunserverCommand.server_cls


@pytest.mark.parametrize("reloader", [False, True])
@pytest.mark.parametrize(
    "problem",
    [
        "missing_cert",
        "missing_key",
        "directory",
        "bad_cert",
        "bad_key",
        "mismatch",
        "encrypted",
        "empty",
    ],
)
def test_certificate_errors(command, certificates, tmp_path, reloader, problem):
    certfile, keyfile = certificates
    if problem == "missing_cert":
        certfile.unlink()
    elif problem == "missing_key":
        keyfile.unlink()
    elif problem == "directory":
        certfile = tmp_path
    elif problem == "bad_cert":
        certfile.write_text("not a certificate")
    elif problem == "bad_key":
        keyfile.write_text("not a key")
    elif problem in {"mismatch", "encrypted"}:
        encryption = (
            serialization.BestAvailableEncryption(b"test-password")
            if problem == "encrypted"
            else serialization.NoEncryption()
        )
        keyfile.write_bytes(
            rsa.generate_private_key(
                public_exponent=65537, key_size=2048
            ).private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                encryption,
            )
        )
    elif problem == "empty":
        certfile = ""
    with patch("django.utils.autoreload.run_with_reloader") as reload:
        with pytest.raises(CommandError, match="Cannot configure HTTPS"):
            invoke(
                command,
                certfile=str(certfile),
                keyfile=str(keyfile),
                use_reloader=reloader,
            )
    reload.assert_not_called()
    command.server_cls.assert_not_called()


def test_unreadable_certificate(command, certificates):
    certfile, keyfile = certificates
    with patch(
        "twisted.python.filepath.FilePath.getContent",
        side_effect=PermissionError("Permission denied"),
    ):
        with pytest.raises(
            CommandError, match="Cannot configure HTTPS.*Permission denied"
        ):
            invoke(command, certfile=str(certfile), keyfile=str(keyfile))
    command.server_cls.assert_not_called()


@pytest.mark.parametrize(
    "debug, options, static",
    [
        (True, {}, True),
        (True, {"use_static_handler": False}, False),
        (False, {}, False),
        (False, {"insecure_serving": True}, True),
    ],
)
def test_tls_staticfiles_options(command, certificates, debug, options, static):
    certfile, keyfile = certificates
    with override_settings(DEBUG=debug, ALLOWED_HOSTS=["localhost"]):
        app = invoke(command, certfile=str(certfile), keyfile=str(keyfile), **options)[
            "application"
        ]
    if static:
        assert isinstance(app, ASGIStaticFilesHandler)
        assert app.application is application
    else:
        assert app is application


def test_tls_endpoint_escapes_paths(command, certificates):
    certfile, keyfile = certificates
    # Exercise drive-letter colons and backslashes even on POSIX, without
    # requiring a filesystem which permits these characters in filenames.
    command.addr, command.port = "::1", "8443"
    endpoint = command.get_endpoints(
        {"certfile": r"C:\certs\local.pem", "keyfile": r"C:\certs\local-key.pem"}
    )[0]
    contents = [certfile.read_bytes(), keyfile.read_bytes()]
    paths = []

    def read_content(filepath):
        paths.append(filepath.path)
        return contents[len(paths) - 1]

    with patch(
        "twisted.python.filepath.FilePath.getContent",
        autospec=True,
        side_effect=read_content,
    ):
        serverFromString(MemoryReactor(), endpoint)
    assert paths[0].endswith(r"C:\certs\local.pem")
    assert paths[1].endswith(r"C:\certs\local-key.pem")


def test_tls_shutdown_message(command, certificates):
    certfile, keyfile = certificates
    command.server_cls.return_value.run.side_effect = KeyboardInterrupt
    invoke(
        command,
        certfile=str(certfile),
        keyfile=str(keyfile),
        shutdown_message="Stopped",
    )
    assert "Stopped" in command.stdout.getvalue()
