# coding: utf8
"""
Tests for the HTTP response section of the ASGI spec
"""
from __future__ import unicode_literals

from unittest import TestCase

from asgiref.inmemory import ChannelLayer
from hypothesis import given
from twisted.test import proto_helpers

from daphne.http_protocol import HTTPFactory
from . import factories, http_strategies, testcases


class TestHTTPResponseSpec(testcases.ASGITestCase):

    def test_minimal_response(self):
        """
        Smallest viable example. Mostly verifies that our response building works.
        """
        message = {'status': 200}
        response = factories.response_for_message(message)
        self.assert_valid_http_response_message(message, response)
        self.assertIn(b'200 OK', response)
        # Assert that the response is the last of the chunks.
        # N.b. at the time of writing, Daphne did not support multiple response chunks,
        # but still sends with Transfer-Encoding: chunked if no Content-Length header
        # is specified (and maybe even if specified).
        self.assertTrue(response.endswith(b'0\r\n\r\n'))

    def test_status_code_required(self):
        """
        Asserts that passing in the 'status' key is required.

        Previous versions of Daphne did not enforce this, so this test is here
        to make sure it stays required.
        """
        with self.assertRaises(ValueError):
            factories.response_for_message({})

    def test_status_code_is_transmitted(self):
        """
        Tests that a custom status code is present in the response.

        We can't really use hypothesis to test all sorts of status codes, because a lot
        of them have meaning that is respected by Twisted. E.g. setting 204 (No Content)
        as a status code results in Twisted discarding the body.
        """
        message = {'status': 201}  # 'Created'
        response = factories.response_for_message(message)
        self.assert_valid_http_response_message(message, response)
        self.assertIn(b'201 Created', response)

    @given(body=http_strategies.http_body())
    def test_body_is_transmitted(self, body):
        message = {'status': 200, 'content': body.encode('ascii')}
        response = factories.response_for_message(message)
        self.assert_valid_http_response_message(message, response)

    @given(headers=http_strategies.headers())
    def test_headers(self, headers):
        # The ASGI spec requires us to lowercase our header names
        message = {'status': 200, 'headers': [(name.lower(), value) for name, value in headers]}
        response = factories.response_for_message(message)
        # The assert_ method does the heavy lifting of checking that headers are
        # as expected.
        self.assert_valid_http_response_message(message, response)

    @given(
        headers=http_strategies.headers(),
        body=http_strategies.http_body()
    )
    def test_kitchen_sink(self, headers, body):
        """
        This tests tries to let Hypothesis find combinations of variables that result
        in breaking our assumptions. But responses are less exciting than responses,
        so there's not a lot going on here.
        """
        message = {
            'status': 202,  # 'Accepted'
            'headers': [(name.lower(), value) for name, value in headers],
            'content': body.encode('ascii')
        }
        response = factories.response_for_message(message)
        self.assert_valid_http_response_message(message, response)


class TestHTTPResponse(TestCase):
    """
    Tests that the HTTP protocol class correctly generates and parses messages.
    """

    def setUp(self):
        self.channel_layer = ChannelLayer()
        self.factory = HTTPFactory(self.channel_layer, send_channel="test!")
        self.proto = self.factory.buildProtocol(('127.0.0.1', 0))
        self.tr = proto_helpers.StringTransport()
        self.proto.makeConnection(self.tr)

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
