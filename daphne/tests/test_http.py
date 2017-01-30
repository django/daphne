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

    def test_basic(self):
        """
        Tests basic HTTP parsing
        """
        # Send a simple request to the protocol
        self.proto.dataReceived(
            b"GET /te%20st-%C3%A0/?foo=+bar HTTP/1.1\r\n" +
            b"Host: somewhere.com\r\n" +
            b"\r\n"
        )
        # Get the resulting message off of the channel layer
        _, message = self.channel_layer.receive(["http.request"])
        self.assertEqual(message['http_version'], "1.1")
        self.assertEqual(message['method'], "GET")
        self.assertEqual(message['scheme'], "http")
        self.assertEqual(message['path'], "/te st-à/")
        self.assertEqual(message['query_string'], b"foo=+bar")
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

    def test_root_path_header(self):
        """
        Tests root path header handling
        """
        # Send a simple request to the protocol
        self.proto.dataReceived(
            b"GET /te%20st-%C3%A0/?foo=bar HTTP/1.1\r\n" +
            b"Host: somewhere.com\r\n" +
            b"Daphne-Root-Path: /foobar%20/bar\r\n" +
            b"\r\n"
        )
        # Get the resulting message off of the channel layer, check root_path
        _, message = self.channel_layer.receive(["http.request"])
        self.assertEqual(message['root_path'], "/foobar /bar")

    def test_http_disconnect_sets_path_key(self):
        """
        Tests http disconnect has the path key set, see https://channels.readthedocs.io/en/latest/asgi.html#disconnect
        """
        # Send a simple request to the protocol
        self.proto.dataReceived(
            b"GET /te%20st-%C3%A0/?foo=bar HTTP/1.1\r\n" +
            b"Host: anywhere.com\r\n" +
            b"\r\n"
        )
        # Get the request message
        _, message = self.channel_layer.receive(["http.request"])

        # Send back an example response
        self.factory.dispatch_reply(
            message['reply_channel'],
            {
                "status": 200,
                "status_text": b"OK",
                "content": b"DISCO",
            }
        )

        # Get the disconnection notification
        _, disconnect_message = self.channel_layer.receive(["http.disconnect"])
        self.assertEqual(disconnect_message['path'], "/te st-à/")

    def test_x_forwarded_for_ignored(self):
        """
        Tests basic HTTP parsing
        """
        self.proto.dataReceived(
            b"GET /te%20st-%C3%A0/?foo=+bar HTTP/1.1\r\n" +
            b"Host: somewhere.com\r\n" +
            b"X-Forwarded-For: 10.1.2.3\r\n" +
            b"X-Forwarded-Port: 80\r\n" +
            b"\r\n"
        )
        # Get the resulting message off of the channel layer
        _, message = self.channel_layer.receive(["http.request"])
        self.assertEqual(message['client'], ['192.168.1.1', 54321])

    def test_x_forwarded_for_parsed(self):
        """
        Tests basic HTTP parsing
        """
        self.factory.proxy_forwarded_address_header = 'X-Forwarded-For'
        self.factory.proxy_forwarded_port_header = 'X-Forwarded-Port'
        self.proto.dataReceived(
            b"GET /te%20st-%C3%A0/?foo=+bar HTTP/1.1\r\n" +
            b"Host: somewhere.com\r\n" +
            b"X-Forwarded-For: 10.1.2.3\r\n" +
            b"X-Forwarded-Port: 80\r\n" +
            b"\r\n"
        )
        # Get the resulting message off of the channel layer
        _, message = self.channel_layer.receive(["http.request"])
        self.assertEqual(message['client'], ['10.1.2.3', 80])

    def test_x_forwarded_for_port_missing(self):
        """
        Tests basic HTTP parsing
        """
        self.factory.proxy_forwarded_address_header = 'X-Forwarded-For'
        self.factory.proxy_forwarded_port_header = 'X-Forwarded-Port'
        self.proto.dataReceived(
            b"GET /te%20st-%C3%A0/?foo=+bar HTTP/1.1\r\n" +
            b"Host: somewhere.com\r\n" +
            b"X-Forwarded-For: 10.1.2.3\r\n" +
            b"\r\n"
        )
        # Get the resulting message off of the channel layer
        _, message = self.channel_layer.receive(["http.request"])
        self.assertEqual(message['client'], ['10.1.2.3', 0])
