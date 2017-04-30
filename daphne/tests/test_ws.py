# coding: utf8
from __future__ import unicode_literals

from hypothesis import assume, given, strategies
from twisted.test import proto_helpers

from asgiref.inmemory import ChannelLayer
from daphne.http_protocol import HTTPFactory
from daphne.tests import http_strategies, testcases, factories


class WebSocketConnection(object):
    """
    Helper class that makes it easier to test Dahpne's WebSocket support.
    """

    def __init__(self):
        self.last_message = None

        self.channel_layer = ChannelLayer()
        self.factory = HTTPFactory(self.channel_layer, send_channel="test!")
        self.proto = self.factory.buildProtocol(('127.0.0.1', 0))
        self.transport = proto_helpers.StringTransport()
        self.proto.makeConnection(self.transport)

    def receive(self, request):
        """
        Low-level method to let Daphne handle HTTP/WebSocket data
        """
        self.proto.dataReceived(request)
        _, self.last_message = self.channel_layer.receive(['websocket.connect'])
        return self.last_message

    def send(self, content):
        """
        Method to respond with a channel message
        """
        if self.last_message is None:
            # Auto-connect for convenience.
            self.connect()
        self.factory.dispatch_reply(self.last_message['reply_channel'], content)
        response = self.transport.value()
        self.transport.clear()
        return response

    def connect(self, path='/', params=None, headers=None):
        """
        High-level method to perform the WebSocket handshake
        """
        request = factories.build_websocket_upgrade(path, params, headers or [])
        message = self.receive(request)
        return message


class TestHandshake(testcases.ASGIWebSocketTestCase):
    """
    Tests for the WebSocket handshake
    """

    def test_minimal(self):
        message = WebSocketConnection().connect()
        self.assert_valid_websocket_connect_message(message)

    @given(
        path=http_strategies.http_path(),
        params=http_strategies.query_params(),
        headers=http_strategies.headers(),
    )
    def test_connection(self, path, params, headers):
        message = WebSocketConnection().connect(path, params, headers)
        self.assert_valid_websocket_connect_message(message, path, params, headers)


class TestSendCloseAccept(testcases.ASGIWebSocketTestCase):
    """
    Tests that, essentially, try to translate the send/close/accept section of the spec into code.
    """

    def test_empty_accept(self):
        response = WebSocketConnection().send({'accept': True})
        self.assert_websocket_upgrade(response)

    @given(text=http_strategies.http_body())
    def test_accept_and_text(self, text):
        response = WebSocketConnection().send({'accept': True, 'text': text})
        self.assert_websocket_upgrade(response, text.encode('ascii'))

    @given(data=http_strategies.binary_payload())
    def test_accept_and_bytes(self, data):
        response = WebSocketConnection().send({'accept': True, 'bytes': data})
        self.assert_websocket_upgrade(response, data)

    def test_accept_false(self):
        response = WebSocketConnection().send({'accept': False})
        self.assert_websocket_denied(response)

    def test_accept_false_with_text(self):
        """
        Tests that even if text is given, the connection is denied.

        We can't easily use Hypothesis to generate data for this test because it's
        hard to detect absence of the body if e.g. Hypothesis would generate a 'GET'
        """
        text = 'foobar'
        response = WebSocketConnection().send({'accept': False, 'text': text})
        self.assert_websocket_denied(response)
        self.assertNotIn(text.encode('ascii'), response)

    def test_accept_false_with_bytes(self):
        """
        Tests that even if data is given, the connection is denied.

        We can't easily use Hypothesis to generate data for this test because it's
        hard to detect absence of the body if e.g. Hypothesis would generate a 'GET'
        """
        data = b'foobar'
        response = WebSocketConnection().send({'accept': False, 'bytes': data})
        self.assert_websocket_denied(response)
        self.assertNotIn(data, response)

    @given(text=http_strategies.http_body())
    def test_just_text(self, text):
        assume(len(text) > 0)
        # If content is sent, accept=True is implied.
        response = WebSocketConnection().send({'text': text})
        self.assert_websocket_upgrade(response, text.encode('ascii'))

    @given(data=http_strategies.binary_payload())
    def test_just_bytes(self, data):
        assume(len(data) > 0)
        # If content is sent, accept=True is implied.
        response = WebSocketConnection().send({'bytes': data})
        self.assert_websocket_upgrade(response, data)

    def test_close_boolean(self):
        response = WebSocketConnection().send({'close': True})
        self.assert_websocket_denied(response)

    @given(number=strategies.integers(min_value=1))
    def test_close_integer(self, number):
        response = WebSocketConnection().send({'close': number})
        self.assert_websocket_denied(response)

    @given(text=http_strategies.http_body())
    def test_close_with_text(self, text):
        assume(len(text) > 0)
        response = WebSocketConnection().send({'close': True, 'text': text})
        self.assert_websocket_upgrade(response, text.encode('ascii'), expect_close=True)

    @given(data=http_strategies.binary_payload())
    def test_close_with_data(self, data):
        assume(len(data) > 0)
        response = WebSocketConnection().send({'close': True, 'bytes': data})
        self.assert_websocket_upgrade(response, data, expect_close=True)


class TestWebSocketProtocol(testcases.ASGIWebSocketTestCase):
    """
    Tests that the WebSocket protocol class correctly generates and parses messages.
    """

    def setUp(self):
        self.connection = WebSocketConnection()

    def test_basic(self):
        # Send a simple request to the protocol and get the resulting message off
        # of the channel layer.
        message = self.connection.receive(
            b"GET /chat HTTP/1.1\r\n"
            b"Host: somewhere.com\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: x3JJHMbDL1EzLkh9GBhXDw==\r\n"
            b"Sec-WebSocket-Protocol: chat, superchat\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"Origin: http://example.com\r\n"
            b"\r\n"
        )
        self.assertEqual(message['path'], "/chat")
        self.assertEqual(message['query_string'], b"")
        self.assertEqual(
            sorted(message['headers']),
            [(b'connection', b'Upgrade'),
             (b'host', b'somewhere.com'),
             (b'origin', b'http://example.com'),
             (b'sec-websocket-key', b'x3JJHMbDL1EzLkh9GBhXDw=='),
             (b'sec-websocket-protocol', b'chat, superchat'),
             (b'sec-websocket-version', b'13'),
             (b'upgrade', b'websocket')]
        )
        self.assert_valid_websocket_connect_message(message, '/chat')

        # Accept the connection
        response = self.connection.send({'accept': True})
        self.assert_websocket_upgrade(response)

        # Send some text
        response = self.connection.send({'text': "Hello World!"})
        self.assertEqual(response, b"\x81\x0cHello World!")

        # Send some bytes
        response = self.connection.send({'bytes': b"\xaa\xbb\xcc\xdd"})
        self.assertEqual(response, b"\x82\x04\xaa\xbb\xcc\xdd")

        # Close the connection
        response = self.connection.send({'close': True})
        self.assertEqual(response, b"\x88\x02\x03\xe8")

    def test_connection_with_file_origin_is_accepted(self):
        message = self.connection.receive(
            b"GET /chat HTTP/1.1\r\n"
            b"Host: somewhere.com\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: x3JJHMbDL1EzLkh9GBhXDw==\r\n"
            b"Sec-WebSocket-Protocol: chat, superchat\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"Origin: file://\r\n"
            b"\r\n"
        )
        self.assertIn((b'origin', b'file://'), message['headers'])
        self.assert_valid_websocket_connect_message(message, '/chat')

        # Accept the connection
        response = self.connection.send({'accept': True})
        self.assert_websocket_upgrade(response)

    def test_connection_with_no_origin_is_accepted(self):
        message = self.connection.receive(
            b"GET /chat HTTP/1.1\r\n"
            b"Host: somewhere.com\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: x3JJHMbDL1EzLkh9GBhXDw==\r\n"
            b"Sec-WebSocket-Protocol: chat, superchat\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"\r\n"
        )

        self.assertNotIn(b'origin', [header_tuple[0] for header_tuple in message['headers']])
        self.assert_valid_websocket_connect_message(message, '/chat')

        # Accept the connection
        response = self.connection.send({'accept': True})
        self.assert_websocket_upgrade(response)
