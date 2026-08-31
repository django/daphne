from unittest import TestCase
from unittest.mock import patch

from daphne.management.commands.runserver import Command


class TestRunserverCommand(TestCase):
    def test_log_action_formats_http_details_before_logging(self):
        command = Command()
        details = {
            "method": "GET",
            "path": "/example/",
            "status": 200,
            "time_taken": 0.01,
            "client": "127.0.0.1:8000",
            "size": 0,
        }

        with patch("daphne.management.commands.runserver.logger.info") as mock_info:
            command.log_action("http", "complete", details)

        mock_info.assert_called_once()
        args, kwargs = mock_info.call_args
        self.assertEqual(len(args), 1)
        self.assertEqual(kwargs, {})
        self.assertIn("HTTP GET /example/ 200 [0.01, 127.0.0.1:8000]", args[0])
