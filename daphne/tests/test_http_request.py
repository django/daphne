# coding: utf8
"""
Tests for the HTTP request section of the ASGI spec
"""
from __future__ import unicode_literals

import unittest
from six.moves.urllib import parse

from asgiref.inmemory import ChannelLayer
from hypothesis import given, assume, settings, HealthCheck
from twisted.test import proto_helpers

from daphne.http_protocol import HTTPFactory
from daphne.tests import testcases, http_strategies
from daphne.tests.factories import message_for_request, content_length_header


class TestHTTPRequestSpec(testcases.ASGITestCase):
    """
    Tests which try to pour the HTTP request section of the ASGI spec into code.
    The heavy lifting is done by the assert_valid_http_request_message function,
    the tests mostly serve to wire up hypothesis so that it exercise it's power to find
    edge cases.
    """

    def test_minimal_request(self):
        """
        Smallest viable example. Mostly verifies that our request building works.
        """
        request_method, request_path = 'GET', '/'
        message = message_for_request(request_method, request_path)

        self.assert_valid_http_request_message(message, request_method, request_path)

    @given(
        request_path=http_strategies.http_path(),
        request_params=http_strategies.query_params()
    )
    def test_get_request(self, request_path, request_params):
        """
        Tests a typical HTTP GET request, with a path and query parameters
        """
        request_method = 'GET'
        message = message_for_request(request_method, request_path, request_params)

        self.assert_valid_http_request_message(
            message, request_method, request_path, request_params=request_params)

    @given(
        request_path=http_strategies.http_path(),
        request_body=http_strategies.http_body()
    )
    def test_post_request(self, request_path, request_body):
        """
        Tests a typical POST request, submitting some data in a body.
        """
        request_method = 'POST'
        headers = [content_length_header(request_body)]
        message = message_for_request(
            request_method, request_path, headers=headers, body=request_body)

        self.assert_valid_http_request_message(
            message, request_method, request_path,
            request_headers=headers, request_body=request_body)

    @given(request_headers=http_strategies.headers())
    def test_headers(self, request_headers):
        """
        Tests that HTTP header fields are handled as specified
        """
        request_method, request_path = 'OPTIONS', '/te st-à/'
        message = message_for_request(request_method, request_path, headers=request_headers)

        self.assert_valid_http_request_message(
            message, request_method, request_path, request_headers=request_headers)

    @given(request_headers=http_strategies.headers())
    def test_duplicate_headers(self, request_headers):
        """
        Tests that duplicate header values are preserved
        """
        assume(len(request_headers) >= 2)
        # Set all header field names to the same value
        header_name = request_headers[0][0]
        duplicated_headers = [(header_name, header[1]) for header in request_headers]

        request_method, request_path = 'OPTIONS', '/te st-à/'
        message = message_for_request(request_method, request_path, headers=duplicated_headers)

        self.assert_valid_http_request_message(
            message, request_method, request_path, request_headers=duplicated_headers)

    @given(
        request_method=http_strategies.http_method(),
        request_path=http_strategies.http_path(),
        request_params=http_strategies.query_params(),
        request_headers=http_strategies.headers(),
        request_body=http_strategies.http_body(),
    )
    # This test is slow enough that on Travis, hypothesis sometimes complains.
    @settings(suppress_health_check=[HealthCheck.too_slow])
    def test_kitchen_sink(
            self, request_method, request_path, request_params, request_headers, request_body):
        """
        Throw everything at channels that we dare. The idea is that if a combination
        of method/path/headers/body would break the spec, hypothesis will eventually find it.
        """
        request_headers.append(content_length_header(request_body))
        message = message_for_request(
            request_method, request_path, request_params, request_headers, request_body)

        self.assert_valid_http_request_message(
            message, request_method, request_path, request_params, request_headers, request_body)

    def test_headers_are_lowercased_and_stripped(self):
        request_method, request_path = 'GET', '/'
        headers = [('MYCUSTOMHEADER', '   foobar    ')]
        message = message_for_request(request_method, request_path, headers=headers)

        self.assert_valid_http_request_message(
            message, request_method, request_path, request_headers=headers)
        # Note that Daphne returns a list of tuples here, which is fine, because the spec
        # asks to treat them interchangeably.
        assert message['headers'] == [(b'mycustomheader', b'foobar')]

    @given(daphne_path=http_strategies.http_path())
    def test_root_path_header(self, daphne_path):
        """
        Tests root_path handling.
        """
        request_method, request_path = 'GET', '/'
        # Daphne-Root-Path must be URL encoded when submitting as HTTP header field
        headers = [('Daphne-Root-Path', parse.quote(daphne_path.encode('utf8')))]
        message = message_for_request(request_method, request_path, headers=headers)

        # Daphne-Root-Path is not included in the returned 'headers' section. So we expect
        # empty headers.
        expected_headers = []
        self.assert_valid_http_request_message(
            message, request_method, request_path, request_headers=expected_headers)
        # And what we're looking for, root_path being set.
        assert message['root_path'] == daphne_path


class TestProxyHandling(unittest.TestCase):
    """
    Tests that concern interaction of Daphne with proxies.

    They live in a separate test case, because they're not part of the spec.
    """

    def setUp(self):
        self.channel_layer = ChannelLayer()
        self.factory = HTTPFactory(self.channel_layer, send_channel="test!")
        self.proto = self.factory.buildProtocol(('127.0.0.1', 0))
        self.tr = proto_helpers.StringTransport()
        self.proto.makeConnection(self.tr)

    def test_x_forwarded_for_ignored(self):
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
