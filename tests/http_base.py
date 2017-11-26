from urllib import parse
from http.client import HTTPConnection
import socket
import subprocess
import time
import unittest

from daphne.test_utils import TestApplication


class DaphneTestCase(unittest.TestCase):
    """
    Base class for Daphne integration test cases.

    Boots up a copy of Daphne on a test port and sends it a request, and
    retrieves the response. Uses a custom ASGI application and temporary files
    to store/retrieve the request/response messages.
    """

    def port_in_use(self, port):
        """
        Tests if a port is in use on the local machine.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
        except socket.error as e:
            if e.errno in [13, 98]:
                return True
            else:
                raise
        else:
            return False
        finally:
            s.close()

    def run_daphne(self, method, path, params, body, responses, headers=None, timeout=1, xff=False):
        """
        Runs Daphne with the given request callback (given the base URL)
        and response messages.
        """
        # Store setup info
        TestApplication.clear_storage()
        TestApplication.save_setup(
            response_messages=responses,
        )
        # Find a free port
        for i in range(11200, 11300):
            if not self.port_in_use(i):
                port = i
                break
        else:
            raise RuntimeError("Cannot find a free port to test on")
        # Launch daphne on that port
        daphne_args = ["daphne", "-p", str(port), "-v", "0"]
        if xff:
            # Optionally enable X-Forwarded-For support.
            daphne_args += ["--proxy-headers"]
        process = subprocess.Popen(daphne_args + ["daphne.test_utils:TestApplication"])
        try:
            for _ in range(100):
                time.sleep(0.1)
                if self.port_in_use(port):
                    break
            else:
                raise RuntimeError("Daphne never came up.")
            # Send it the request. We have to do this the long way to allow
            # duplicate headers.
            conn = HTTPConnection("127.0.0.1", port, timeout=timeout)
            # Make sure path is urlquoted and add any params
            path = parse.quote(path)
            if params:
                path += "?" + parse.urlencode(params, doseq=True)
            conn.putrequest(method, path, skip_accept_encoding=True, skip_host=True)
            # Manually send over headers (encoding any non-safe values as best we can)
            if headers:
                for header_name, header_value in headers:
                    conn.putheader(header_name.encode("utf8"), header_value.encode("utf8"))
            # Send body if provided.
            if body:
                conn.putheader("Content-Length", str(len(body)))
                conn.endheaders(message_body=body)
            else:
                conn.endheaders()
            response = conn.getresponse()
        finally:
            # Shut down daphne
            process.terminate()
        # Load the information
        inner_result = TestApplication.load_result()
        # Return the inner result and the response
        return inner_result, response

    def run_daphne_request(self, method, path, params=None, body=None, headers=None, xff=False):
        """
        Convenience method for just testing request handling.
        Returns (scope, messages)
        """
        inner_result, _ = self.run_daphne(
            method=method,
            path=path,
            params=params,
            body=body,
            headers=headers,
            xff=xff,
            responses=[{"type": "http.response", "status": 200, "content": b"OK"}],
        )
        return inner_result["scope"], inner_result["messages"]

    def tearDown(self):
        """
        Ensures any storage files are cleared.
        """
        TestApplication.clear_storage()

    def assert_is_ip_address(self, address):
        """
        Tests whether a given address string is a valid IPv4 or IPv6 address.
        """
        try:
            socket.inet_aton(address)
        except socket.error:
            self.fail("'%s' is not a valid IP address." % address)

    def assert_key_sets(self, required_keys, optional_keys, actual_keys):
        """
        Asserts that all required_keys are in actual_keys, and that there
        are no keys in actual_keys that aren't required or optional.
        """
        present_keys = set(actual_keys)
        # Make sure all required keys are present
        self.assertTrue(required_keys <= present_keys)
        # Assert that no other keys are present
        self.assertEqual(
            set(),
            present_keys - required_keys - optional_keys,
        )

    def assert_valid_path(self, path, request_path):
        """
        Checks the path is valid and already url-decoded.
        """
        self.assertIsInstance(path, str)
        self.assertEqual(path, request_path)
        # Assert that it's already url decoded
        self.assertEqual(path, parse.unquote(path))

    def assert_valid_address_and_port(self, host):
        """
        Asserts the value is a valid (host, port) tuple.
        """
        address, port = host
        self.assertIsInstance(address, str)
        self.assert_is_ip_address(address)
        self.assertIsInstance(port, int)


# class ASGIHTTPTestCase(ASGITestCaseBase):
#     """
#     Test case with helpers for verifying HTTP channel messages
#     """


#     def assert_valid_http_response_message(self, message, response):
#         self.assertTrue(message)
#         self.assertTrue(response.startswith(b"HTTP"))

#         status_code_bytes = str(message["status"]).encode("ascii")
#         self.assertIn(status_code_bytes, response)

#         if "content" in message:
#             self.assertIn(message["content"], response)

#         # Check that headers are in the given order.
#         # N.b. HTTP spec only enforces that the order of header values is kept, but
#         # the ASGI spec requires that order of all headers is kept. This code
#         # checks conformance with the stricter ASGI spec.
#         if "headers" in message:
#             for name, value in message["headers"]:
#                 expected_header = factories.header_line(name, value)
#                 # Daphne or Twisted turn our lower cased header names ('foo-bar') into title
#                 # case ('Foo-Bar'). So technically we want to to match that the header name is
#                 # present while ignoring casing, and want to ensure the value is present without
#                 # altered casing. The approach below does this well enough.
#                 self.assertIn(expected_header.lower(), response.lower())
#                 self.assertIn(value.encode("ascii"), response)


# class ASGIWebSocketTestCase(ASGITestCaseBase):
#     """
#     Test case with helpers for verifying WebSocket channel messages
#     """

#     def assert_websocket_upgrade(self, response, body=b"", expect_close=False):
#         self.assertIn(b"HTTP/1.1 101 Switching Protocols", response)
#         self.assertIn(b"Sec-WebSocket-Accept: HSmrc0sMlYUkAGmm5OPpG2HaGWk=\r\n", response)
#         self.assertIn(body, response)
#         self.assertEqual(expect_close, response.endswith(b"\x88\x02\x03\xe8"))

#     def assert_websocket_denied(self, response):
#         self.assertIn(b"HTTP/1.1 403", response)

#     def assert_valid_websocket_connect_message(
#             self, channel_message, request_path="/", request_params=None, request_headers=None):
#         """
#         Asserts that a given channel message conforms to the HTTP request section of the ASGI spec.
#         """

#         self.assertTrue(channel_message)

#         self.assert_presence_of_message_keys(
#             channel_message.keys(),
#             {"reply_channel", "path", "headers", "order"},
#             {"scheme", "query_string", "root_path", "client", "server"})

#         # == Assertions about required channel_message fields ==
#         self.assert_valid_reply_channel(channel_message["reply_channel"])
#         self.assert_valid_path(channel_message["path"], request_path)

#         order = channel_message["order"]
#         self.assertIsInstance(order, int)
#         self.assertEqual(order, 0)

#         # Ordering of header names is not important, but the order of values for a header
#         # name is. To assert whether that order is kept, we transform the request
#         # headers and the channel message headers into a set
#         # {('name1': 'value1,value2'), ('name2': 'value3')} and check if they're equal.
#         # Note that unlike for HTTP, Daphne never gives out individual header values; instead we
#         # get one string per header field with values separated by comma.
#         transformed_request_headers = defaultdict(list)
#         for name, value in (request_headers or []):
#             expected_name = name.lower().strip().encode("ascii")
#             expected_value = value.strip().encode("ascii")
#             transformed_request_headers[expected_name].append(expected_value)
#         final_request_headers = {
#             (name, b",".join(value)) for name, value in transformed_request_headers.items()
#         }

#         # Websockets carry a lot of additional header fields, so instead of verifying that
#         # headers look exactly like expected, we just check that the expected header fields
#         # and values are present - additional header fields (e.g. Sec-WebSocket-Key) are allowed
#         # and not tested for.
#         assert final_request_headers.issubset(set(channel_message["headers"]))

#         # == Assertions about optional channel_message fields ==
#         scheme = channel_message.get("scheme")
#         if scheme:
#             self.assertIsInstance(scheme, six.text_type)
#             self.assertIn(scheme, ["ws", "wss"])

#         query_string = channel_message.get("query_string")
#         if query_string:
#             # Assert that query_string is a byte string and still url encoded
#             self.assertIsInstance(query_string, six.binary_type)
#             self.assertEqual(query_string, parse.urlencode(request_params or []).encode("ascii"))

#         root_path = channel_message.get("root_path")
#         if root_path is not None:
#             self.assertIsInstance(root_path, six.text_type)

#         client = channel_message.get("client")
#         if client is not None:
#             self.assert_valid_address_and_port(channel_message["client"])

#         server = channel_message.get("server")
#         if server is not None:
#             self.assert_valid_address_and_port(channel_message["server"])
