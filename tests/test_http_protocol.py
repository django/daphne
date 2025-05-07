import unittest

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
