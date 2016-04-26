# coding: utf8
from __future__ import unicode_literals
from unittest import TestCase
from asgiref.inmemory import ChannelLayer
from twisted.test import proto_helpers

from ..http_protocol import HTTPFactory


class TestHTTPProtocol(TestCase):
    """
    Tests that the HTTP protocol class correctly generates and parses messages.
    """

    def setUp(self):
        self.channel_layer = ChannelLayer()
        self.factory = HTTPFactory(self.channel_layer)
        self.proto = self.factory.buildProtocol(('127.0.0.1', 0))
        self.tr = proto_helpers.StringTransport()
        self.proto.makeConnection(self.tr)

    def assertStartsWith(self, data, prefix):
        real_prefix = data[:len(prefix)]
        self.assertEqual(real_prefix, prefix)

    def test_basic(self):
        """
        Tests basic HTTP parsing
        """
        # Send a simple request to the protocol
        self.proto.dataReceived(
            b"GET /te%20st-%C3%A0/?foo=bar HTTP/1.1\r\n" +
            b"Host: somewhere.com\r\n" +
            b"\r\n"
        )
        # Get the resulting message off of the channel layer
        _, message = self.channel_layer.receive_many(["http.request"])
        self.assertEqual(message['http_version'], "1.1")
        self.assertEqual(message['method'], "GET")
        self.assertEqual(message['scheme'], "http")
        self.assertEqual(message['path'], "/te st-à/")
        self.assertEqual(message['query_string'], "foo=bar")
        self.assertEqual(message['headers'], [(b"host", b"somewhere.com")])
        self.assertFalse(message.get("body", None))
        self.assertTrue(message['reply_channel'])
        # Send back an example response
        self.factory.dispatch_reply(
            message['reply_channel'],
            {
                "status": 201,
                "status_text": b"Created",
                "content": b"OH HAI",
                "headers": [[b"X-Test", b"Boom!"]],
            }
        )
        # Make sure that comes back right on the protocol
        self.assertEqual(self.tr.value(), b"HTTP/1.1 201 Created\r\nTransfer-Encoding: chunked\r\nX-Test: Boom!\r\n\r\n6\r\nOH HAI\r\n0\r\n\r\n")
