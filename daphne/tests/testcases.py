"""
Contains a test case class to allow verifying ASGI messages
"""
from __future__ import unicode_literals

from collections import defaultdict

import six
import socket
from six.moves.urllib import parse
import unittest


class ASGITestCase(unittest.TestCase):
    """
    Test case with helpers for ASGI message verification
    """

    def assert_is_ip_address(self, address):
        """
        Tests whether a given address string is a valid IPv4 or IPv6 address.
        """
        try:
            socket.inet_aton(address)
        except socket.error:
            self.fail("'%s' is not a valid IP address." % address)

    def assert_valid_http_request_message(
            self, channel_message, request_method, request_path,
            request_params=None, request_headers=None, request_body=None):
        """
        Asserts that a given channel message conforms to the HTTP request section of the ASGI spec.
        """

        self.assertTrue(channel_message)

        # == General assertions about expected dictionary keys being present ==
        message_keys = set(channel_message.keys())
        required_message_keys = {
            'reply_channel', 'http_version', 'method', 'path', 'query_string', 'headers',
        }
        optional_message_keys = {
            'scheme', 'root_path', 'body', 'body_channel', 'client', 'server'
        }
        self.assertTrue(required_message_keys <= message_keys)
        # Assert that no other keys are present
        self.assertEqual(set(), message_keys - required_message_keys - optional_message_keys)

        # == Assertions about required channel_message fields ==
        reply_channel = channel_message['reply_channel']
        self.assertIsInstance(reply_channel, six.text_type)
        self.assertTrue(reply_channel.startswith('http.response!'))

        http_version = channel_message['http_version']
        self.assertIsInstance(http_version, six.text_type)
        self.assertIn(http_version, ['1.0', '1.1', '1.2'])

        method = channel_message['method']
        self.assertIsInstance(method, six.text_type)
        self.assertTrue(method.isupper())
        self.assertEqual(channel_message['method'], request_method)

        path = channel_message['path']
        self.assertIsInstance(path, six.text_type)
        self.assertEqual(path, request_path)
        # Assert that it's already url decoded
        self.assertEqual(path, parse.unquote(path))

        query_string = channel_message['query_string']
        # Assert that query_string is a byte string and still url encoded
        self.assertIsInstance(query_string, six.binary_type)
        self.assertEqual(query_string, parse.urlencode(request_params or []).encode('ascii'))

        # Ordering of header names is not important, but the order of values for a header
        # name is. To assert whether that order is kept, we transform both the request
        # headers and the channel message headers into a dictionary
        # {name: [value1, value2, ...]} and check if they're equal.
        transformed_message_headers = defaultdict(list)
        for name, value in channel_message['headers']:
            transformed_message_headers[name].append(value)

        transformed_request_headers = defaultdict(list)
        for name, value in (request_headers or []):
            expected_name = name.lower().strip().encode('ascii')
            expected_value = value.strip().encode('ascii')
            transformed_request_headers[expected_name].append(expected_value)

        self.assertEqual(transformed_message_headers, transformed_request_headers)

        # == Assertions about optional channel_message fields ==

        scheme = channel_message.get('scheme')
        if scheme is not None:
            self.assertIsInstance(scheme, six.text_type)
            self.assertTrue(scheme)  # May not be empty

        root_path = channel_message.get('root_path')
        if root_path is not None:
            self.assertIsInstance(root_path, six.text_type)

        body = channel_message.get('body')
        # Ensure we test for presence of 'body' if a request body was given
        if request_body is not None or body is not None:
            self.assertIsInstance(body, six.binary_type)
            self.assertEqual(body, (request_body or '').encode('ascii'))

        body_channel = channel_message.get('body_channel')
        if body_channel is not None:
            self.assertIsInstance(body_channel, six.text_type)
            self.assertIn('?', body_channel)

        client = channel_message.get('client')
        if client is not None:
            client_host, client_port = client
            self.assertIsInstance(client_host, six.text_type)
            self.assert_is_ip_address(client_host)
            self.assertIsInstance(client_port, int)

        server = channel_message.get('server')
        if server is not None:
            server_host, server_port = channel_message['server']
            self.assertIsInstance(server_host, six.text_type)
            self.assert_is_ip_address(server_host)
            self.assertIsInstance(server_port, int)
