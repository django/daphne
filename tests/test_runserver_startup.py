from unittest import TestCase

from django.core.management import CommandError

from daphne.management.commands.runserver import Command


class TestRunserverCommand(TestCase):
    class AbortedServer:
        abort_start = True

        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.ran = False

        def run(self):
            self.ran = True

    def test_run_daphne_raises_command_error_when_start_aborts(self):
        command = Command()
        command.server_cls = self.AbortedServer
        command.http_timeout = None
        command.websocket_handshake_timeout = 5

        with self.assertRaisesRegex(CommandError, "Daphne failed to start."):
            command.run_daphne(
                application=object(),
                endpoints=["tcp:port=8000:interface=127.0.0.1"],
                options={"use_reloader": False},
                root_path="",
            )

        self.assertTrue(command.server.ran)
        self.assertTrue(command.server.init_kwargs["signal_handlers"])
