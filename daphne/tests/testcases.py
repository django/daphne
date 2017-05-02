"""
Contains a test case class to allow verifying ASGI messages
"""
from __future__ import unicode_literals

from collections import defaultdict
import six
from six.moves.urllib import parse
import socket
import unittest

from . import factories


class ASGITestCaseBase(unittest.TestCase):
    """
    Base class for our test classes which contains shared method.
    """

    def assert_is_ip_address(self, address):
        """
        Tests whether a given address string is a valid IPv4 or IPv6 address.
        """
        try:
            socket.inet_aton(address)
        except socket.error:
            self.fail("'%s' is not a valid IP address." % address)

    def assert_presence_of_message_keys(self, keys, required_keys, optional_keys):
        present_keys = set(keys)
        self.assertTrue(required_keys <= present_keys)
        # Assert that no other keys are present
        self.assertEqual(set(), present_keys - required_keys - optional_keys)

    def assert_valid_reply_channel(self, reply_channel):
        self.assertIsInstance(reply_channel, six.text_type)
        # The reply channel is decided by the server.
        self.assertTrue(reply_channel.startswith('test!'))

    def assert_valid_path(self, path, request_path):
        self.assertIsInstance(path, six.text_type)
        self.assertEqual(path, request_path)
        # Assert that it's already url decoded
        self.assertEqual(path, parse.unquote(path))

    def assert_valid_address_and_port(self, host):
        address, port = host
        self.assertIsInstance(address, six.text_type)
        self.assert_is_ip_address(address)
        self.assertIsInstance(port, int)


class ASGIHTTPTestCase(ASGITestCaseBase):
    """
    Test case with helpers for verifying HTTP channel messages
    """

    def assert_valid_http_request_message(
            self, channel_message, request_method, request_path,
            request_params=None, request_headers=None, request_body=None):
        """
        Asserts that a given channel message conforms to the HTTP request section of the ASGI spec.
        """

        self.assertTrue(channel_message)

        self.assert_presence_of_message_keys(
            channel_message.keys(),
            {'reply_channel', 'http_version', 'method', 'path', 'query_string', 'headers'},
            {'scheme', 'root_path', 'body', 'body_channel', 'client', 'server'})

        # == Assertions about required channel_message fields ==
        self.assert_valid_reply_channel(channel_message['reply_channel'])
        self.assert_valid_path(channel_message['path'], request_path)

        http_version = channel_message['http_version']
        self.assertIsInstance(http_version, six.text_type)
        self.assertIn(http_version, ['1.0', '1.1', '1.2'])

        method = channel_message['method']
        self.assertIsInstance(method, six.text_type)
        self.assertTrue(method.isupper())
        self.assertEqual(channel_message['method'], request_method)

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
            self.assert_valid_address_and_port(channel_message['client'])

        server = channel_message.get('server')
        if server is not None:
            self.assert_valid_address_and_port(channel_message['server'])

    def assert_valid_http_response_message(self, message, response):
        self.assertTrue(message)
        self.assertTrue(response.startswith(b'HTTP'))

        status_code_bytes = six.text_type(message['status']).encode('ascii')
        self.assertIn(status_code_bytes, response)

        if 'content' in message:
            self.assertIn(message['content'], response)

        # Check that headers are in the given order.
        # N.b. HTTP spec only enforces that the order of header values is kept, but
        # the ASGI spec requires that order of all headers is kept. This code
        # checks conformance with the stricter ASGI spec.
        if 'headers' in message:
            for name, value in message['headers']:
                expected_header = factories.header_line(name, value)
                # Daphne or Twisted turn our lower cased header names ('foo-bar') into title
                # case ('Foo-Bar'). So technically we want to to match that the header name is
                # present while ignoring casing, and want to ensure the value is present without
                # altered casing. The approach below does this well enough.
                self.assertIn(expected_header.lower(), response.lower())
                self.assertIn(value.encode('ascii'), response)


class ASGIWebSocketTestCase(ASGITestCaseBase):
    """
    Test case with helpers for verifying WebSocket channel messages
    """

    def assert_websocket_upgrade(self, response, body=b'', expect_close=False):
        self.assertIn(b"HTTP/1.1 101 Switching Protocols", response)
        self.assertIn(b"Sec-WebSocket-Accept: HSmrc0sMlYUkAGmm5OPpG2HaGWk=\r\n", response)
        self.assertIn(body, response)
        self.assertEqual(expect_close, response.endswith(b"\x88\x02\x03\xe8"))

    def assert_websocket_denied(self, response):
        self.assertIn(b'HTTP/1.1 403', response)

    def assert_valid_websocket_connect_message(
            self, channel_message, request_path='/', request_params=None, request_headers=None):
        """
        Asserts that a given channel message conforms to the HTTP request section of the ASGI spec.
        """

        self.assertTrue(channel_message)

        self.assert_presence_of_message_keys(
            channel_message.keys(),
            {'reply_channel', 'path', 'headers', 'order'},
            {'scheme', 'query_string', 'root_path', 'client', 'server'})

        # == Assertions about required channel_message fields ==
        self.assert_valid_reply_channel(channel_message['reply_channel'])
        self.assert_valid_path(channel_message['path'], request_path)

        order = channel_message['order']
        self.assertIsInstance(order, int)
        self.assertEqual(order, 0)

        # Ordering of header names is not important, but the order of values for a header
        # name is. To assert whether that order is kept, we transform the request
        # headers and the channel message headers into a set
        # {('name1': 'value1,value2'), ('name2': 'value3')} and check if they're equal.
        # Note that unlike for HTTP, Daphne never gives out individual header values; instead we
        # get one string per header field with values separated by comma.
        transformed_request_headers = defaultdict(list)
        for name, value in (request_headers or []):
            expected_name = name.lower().strip().encode('ascii')
            expected_value = value.strip().encode('ascii')
            transformed_request_headers[expected_name].append(expected_value)
        final_request_headers = {
            (name, b','.join(value)) for name, value in transformed_request_headers.items()
        }

        # Websockets carry a lot of additional header fields, so instead of verifying that
        # headers look exactly like expected, we just check that the expected header fields
        # and values are present - additional header fields (e.g. Sec-WebSocket-Key) are allowed
        # and not tested for.
        assert final_request_headers.issubset(set(channel_message['headers']))

        # == Assertions about optional channel_message fields ==
        scheme = channel_message.get('scheme')
        if scheme:
            self.assertIsInstance(scheme, six.text_type)
            self.assertIn(scheme, ['ws', 'wss'])

        query_string = channel_message.get('query_string')
        if query_string:
            # Assert that query_string is a byte string and still url encoded
            self.assertIsInstance(query_string, six.binary_type)
            self.assertEqual(query_string, parse.urlencode(request_params or []).encode('ascii'))

        root_path = channel_message.get('root_path')
        if root_path is not None:
            self.assertIsInstance(root_path, six.text_type)

        client = channel_message.get('client')
        if client is not None:
            self.assert_valid_address_and_port(channel_message['client'])

        server = channel_message.get('server')
        if server is not None:
            self.assert_valid_address_and_port(channel_message['server'])
