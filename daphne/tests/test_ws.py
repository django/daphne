# coding: utf8
from __future__ import unicode_literals
from unittest import TestCase
from asgiref.inmemory import ChannelLayer
from twisted.test import proto_helpers

from daphne.http_protocol import HTTPFactory


class TestWebSocketProtocol(TestCase):
    """
    Tests that the WebSocket protocol class correcly generates and parses messages.
    """

    def setUp(self):
        self.channel_layer = ChannelLayer()
        self.factory = HTTPFactory(self.channel_layer, send_channel="test!")
        self.proto = self.factory.buildProtocol(('127.0.0.1', 0))
        self.tr = proto_helpers.StringTransport()
        self.proto.makeConnection(self.tr)

    def test_basic(self):
        # Send a simple request to the protocol
        self.proto.dataReceived(
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
        # Get the resulting message off of the channel layer
        _, message = self.channel_layer.receive(["websocket.connect"])
        self.assertEqual(message['path'], "/chat")
        self.assertEqual(message['query_string'], "")
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
        self.assertTrue(message['reply_channel'].startswith("test!"))

        # Accept the connection
        self.factory.dispatch_reply(
            message['reply_channel'],
            {'accept': True}
        )

        # Make sure that we get a 101 Switching Protocols back
        response = self.tr.value()
        self.assertIn(b"HTTP/1.1 101 Switching Protocols\r\n", response)
        self.assertIn(b"Sec-WebSocket-Accept: HSmrc0sMlYUkAGmm5OPpG2HaGWk=\r\n", response)
        self.tr.clear()

        # Send some text
        self.factory.dispatch_reply(
            message['reply_channel'],
            {'text': "Hello World!"}
        )

        response = self.tr.value()
        self.assertEqual(response, b"\x81\x0cHello World!")
        self.tr.clear()

        # Send some bytes
        self.factory.dispatch_reply(
            message['reply_channel'],
            {'bytes': b"\xaa\xbb\xcc\xdd"}
        )

        response = self.tr.value()
        self.assertEqual(response, b"\x82\x04\xaa\xbb\xcc\xdd")
        self.tr.clear()

        # Close the connection
        self.factory.dispatch_reply(
            message['reply_channel'],
            {'close': True}
        )

        response = self.tr.value()
        self.assertEqual(response, b"\x88\x02\x03\xe8")
        self.tr.clear()

    def test_connection_with_file_origin_is_accepted(self):
        # Send a simple request to the protocol
        self.proto.dataReceived(
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

        # Get the resulting message off of the channel layer
        _, message = self.channel_layer.receive(["websocket.connect"])
        self.assertIn((b'origin', b'file://'), message['headers'])
        self.assertTrue(message['reply_channel'].startswith("test!"))

        # Accept the connection
        self.factory.dispatch_reply(
            message['reply_channel'],
            {'accept': True}
        )

        # Make sure that we get a 101 Switching Protocols back
        response = self.tr.value()
        self.assertIn(b"HTTP/1.1 101 Switching Protocols\r\n", response)
        self.assertIn(b"Sec-WebSocket-Accept: HSmrc0sMlYUkAGmm5OPpG2HaGWk=\r\n", response)

    def test_connection_with_no_origin_is_accepted(self):
        # Send a simple request to the protocol
        self.proto.dataReceived(
            b"GET /chat HTTP/1.1\r\n"
            b"Host: somewhere.com\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: x3JJHMbDL1EzLkh9GBhXDw==\r\n"
            b"Sec-WebSocket-Protocol: chat, superchat\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"\r\n"
        )

        # Get the resulting message off of the channel layer
        _, message = self.channel_layer.receive(["websocket.connect"])
        self.assertNotIn(b'origin', [header_tuple[0] for header_tuple in message['headers']])
        self.assertTrue(message['reply_channel'].startswith("test!"))

        # Accept the connection
        self.factory.dispatch_reply(
            message['reply_channel'],
            {'accept': True}
        )

        # Make sure that we get a 101 Switching Protocols back
        response = self.tr.value()
        self.assertIn(b"HTTP/1.1 101 Switching Protocols\r\n", response)
        self.assertIn(b"Sec-WebSocket-Accept: HSmrc0sMlYUkAGmm5OPpG2HaGWk=\r\n", response)
