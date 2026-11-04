import unittest
from unittest.mock import Mock

from daphne.http_protocol import WebRequest


class MockServer:
    """
    Mock server object for testing.
    """

    def protocol_connected(self, *args, **kwargs):
        pass


class MockFactory:
    """
    Mock factory object for testing.
    """

    def __init__(self):
        self.server = MockServer()


class MockChannel:
    """
    Mock channel object for testing.
    """

    def __init__(self):
        self.factory = MockFactory()
        self.transport = None

    def getPeer(self, *args, **kwargs):
        return "peer"

    def getHost(self, *args, **kwargs):
        return "host"


class TestHTTPProtocol(unittest.TestCase):
    """
    Tests the HTTP protocol classes.
    """

    def test_web_request_initialisation(self):
        channel = MockChannel()
        request = WebRequest(channel)
        self.assertIsNone(request.client_addr)
        self.assertIsNone(request.server_addr)

    def test_handle_exception_aborts_connection_after_response_started(self):
        channel = MockChannel()
        channel.transport = Mock()

        request = WebRequest(channel)
        request._response_started = True

        request.handle_exception(RuntimeError("boom"))

        channel.transport.abortConnection.assert_called_once_with()

    def test_handle_exception_returns_500_before_response_started(self):
        channel = MockChannel()
        request = WebRequest(channel)

        request.basic_error = Mock()

        request.handle_exception(RuntimeError("boom"))

        request.basic_error.assert_called_once_with(
            500,
            b"Internal Server Error",
            "Exception inside application.",
        )
