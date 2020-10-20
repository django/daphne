# coding: utf8

import collections
from urllib import parse

import http_strategies
from http_base import DaphneTestCase
from hypothesis import assume, given, settings


class TestHTTPRequest(DaphneTestCase):
    """
    Tests the HTTP request handling.
    """

    def assert_valid_http_scope(
        self, scope, method, path, params=None, headers=None, scheme=None
    ):
        """
        Checks that the passed scope is a valid ASGI HTTP scope regarding types
        and some urlencoding things.
        """
        # Check overall keys
        self.assert_key_sets(
            required_keys={
                "type",
                "http_version",
                "method",
                "path",
                "raw_path",
                "query_string",
                "headers",
            },
            optional_keys={"scheme", "root_path", "client", "server"},
            actual_keys=scope.keys(),
        )
        # Check that it is the right type
        self.assertEqual(scope["type"], "http")
        # Method (uppercased unicode string)
        self.assertIsInstance(scope["method"], str)
        self.assertEqual(scope["method"], method.upper())
        # Path
        self.assert_valid_path(scope["path"])
        # HTTP version
        self.assertIn(scope["http_version"], ["1.0", "1.1", "1.2"])
        # Scheme
        self.assertIn(scope["scheme"], ["http", "https"])
        if scheme:
            self.assertEqual(scheme, scope["scheme"])
        # Query string (byte string and still url encoded)
        query_string = scope["query_string"]
        self.assertIsInstance(query_string, bytes)
        if params:
            self.assertEqual(
                query_string, parse.urlencode(params or []).encode("ascii")
            )
        # Ordering of header names is not important, but the order of values for a header
        # name is. To assert whether that order is kept, we transform both the request
        # headers and the channel message headers into a dictionary
        # {name: [value1, value2, ...]} and check if they're equal.
        transformed_scope_headers = collections.defaultdict(list)
        for name, value in scope["headers"]:
            transformed_scope_headers[name].append(value)
        transformed_request_headers = collections.defaultdict(list)
        for name, value in headers or []:
            expected_name = name.lower().strip()
            expected_value = value.strip()
            transformed_request_headers[expected_name].append(expected_value)
        for name, value in transformed_request_headers.items():
            self.assertIn(name, transformed_scope_headers)
            self.assertEqual(value, transformed_scope_headers[name])
        # Root path
        self.assertIsInstance(scope.get("root_path", ""), str)
        # Client and server addresses
        client = scope.get("client")
        if client is not None:
            self.assert_valid_address_and_port(client)
        server = scope.get("server")
        if server is not None:
            self.assert_valid_address_and_port(server)

    def assert_valid_http_request_message(self, message, body=None):
        """
        Asserts that a message is a valid http.request message
        """
        # Check overall keys
        self.assert_key_sets(
            required_keys={"type"},
            optional_keys={"body", "more_body"},
            actual_keys=message.keys(),
        )
        # Check that it is the right type
        self.assertEqual(message["type"], "http.request")
        # If there's a body present, check its type
        self.assertIsInstance(message.get("body", b""), bytes)
        if body is not None:
            self.assertEqual(body, message.get("body", b""))

    def test_minimal_request(self):
        """
        Smallest viable example. Mostly verifies that our request building works.
        """
        scope, messages = self.run_daphne_request("GET", "/")
        self.assert_valid_http_scope(scope, "GET", "/")
        self.assert_valid_http_request_message(messages[0], body=b"")

    @given(
        request_path=http_strategies.http_path(),
        request_params=http_strategies.query_params(),
    )
    @settings(max_examples=5, deadline=5000)
    def test_get_request(self, request_path, request_params):
        """
        Tests a typical HTTP GET request, with a path and query parameters
        """
        scope, messages = self.run_daphne_request(
            "GET", request_path, params=request_params
        )
        self.assert_valid_http_scope(scope, "GET", request_path, params=request_params)
        self.assert_valid_http_request_message(messages[0], body=b"")

    @given(
        request_path=http_strategies.http_path(),
        request_body=http_strategies.http_body(),
    )
    @settings(max_examples=5, deadline=5000)
    def test_post_request(self, request_path, request_body):
        """
        Tests a typical HTTP POST request, with a path and body.
        """
        scope, messages = self.run_daphne_request(
            "POST", request_path, body=request_body
        )
        self.assert_valid_http_scope(scope, "POST", request_path)
        self.assert_valid_http_request_message(messages[0], body=request_body)

    def test_raw_path(self):
        """
        Tests that /foo%2Fbar produces raw_path and a decoded path
        """
        scope, _ = self.run_daphne_request("GET", "/foo%2Fbar")
        self.assertEqual(scope["path"], "/foo/bar")
        self.assertEqual(scope["raw_path"], b"/foo%2Fbar")

    @given(request_headers=http_strategies.headers())
    @settings(max_examples=5, deadline=5000)
    def test_headers(self, request_headers):
        """
        Tests that HTTP header fields are handled as specified
        """
        request_path = parse.quote("/te st-à/")
        scope, messages = self.run_daphne_request(
            "OPTIONS", request_path, headers=request_headers
        )
        self.assert_valid_http_scope(
            scope, "OPTIONS", request_path, headers=request_headers
        )
        self.assert_valid_http_request_message(messages[0], body=b"")

    @given(request_headers=http_strategies.headers())
    @settings(max_examples=5, deadline=5000)
    def test_duplicate_headers(self, request_headers):
        """
        Tests that duplicate header values are preserved
        """
        # Make sure there's duplicate headers
        assume(len(request_headers) >= 2)
        header_name = request_headers[0][0]
        duplicated_headers = [(header_name, header[1]) for header in request_headers]
        # Run the request
        request_path = parse.quote("/te st-à/")
        scope, messages = self.run_daphne_request(
            "OPTIONS", request_path, headers=duplicated_headers
        )
        self.assert_valid_http_scope(
            scope, "OPTIONS", request_path, headers=duplicated_headers
        )
        self.assert_valid_http_request_message(messages[0], body=b"")

    @given(
        request_method=http_strategies.http_method(),
        request_path=http_strategies.http_path(),
        request_params=http_strategies.query_params(),
        request_headers=http_strategies.headers(),
        request_body=http_strategies.http_body(),
    )
    @settings(max_examples=2, deadline=5000)
    def test_kitchen_sink(
        self,
        request_method,
        request_path,
        request_params,
        request_headers,
        request_body,
    ):
        """
        Throw everything at Daphne that we dare. The idea is that if a combination
        of method/path/headers/body would break the spec, hypothesis will eventually find it.
        """
        scope, messages = self.run_daphne_request(
            request_method,
            request_path,
            params=request_params,
            headers=request_headers,
            body=request_body,
        )
        self.assert_valid_http_scope(
            scope,
            request_method,
            request_path,
            params=request_params,
            headers=request_headers,
        )
        self.assert_valid_http_request_message(messages[0], body=request_body)

    def test_headers_are_lowercased_and_stripped(self):
        """
        Make sure headers are normalized as the spec says they are.
        """
        headers = [(b"MYCUSTOMHEADER", b"   foobar    ")]
        scope, messages = self.run_daphne_request("GET", "/", headers=headers)
        self.assert_valid_http_scope(scope, "GET", "/", headers=headers)
        self.assert_valid_http_request_message(messages[0], body=b"")
        # Note that Daphne returns a list of tuples here, which is fine, because the spec
        # asks to treat them interchangeably.
        assert [list(x) for x in scope["headers"]] == [[b"mycustomheader", b"foobar"]]

    @given(daphne_path=http_strategies.http_path())
    @settings(max_examples=5, deadline=5000)
    def test_root_path_header(self, daphne_path):
        """
        Tests root_path handling.
        """
        # Daphne-Root-Path must be URL encoded when submitting as HTTP header field
        headers = [("Daphne-Root-Path", parse.quote(daphne_path.encode("utf8")))]
        scope, messages = self.run_daphne_request("GET", "/", headers=headers)
        # Daphne-Root-Path is not included in the returned 'headers' section. So we expect
        # empty headers.
        self.assert_valid_http_scope(scope, "GET", "/", headers=[])
        self.assert_valid_http_request_message(messages[0], body=b"")
        # And what we're looking for, root_path being set.
        assert scope["root_path"] == daphne_path

    def test_x_forwarded_for_ignored(self):
        """
        Make sure that, by default, X-Forwarded-For is ignored.
        """
        headers = [[b"X-Forwarded-For", b"10.1.2.3"], [b"X-Forwarded-Port", b"80"]]
        scope, messages = self.run_daphne_request("GET", "/", headers=headers)
        self.assert_valid_http_scope(scope, "GET", "/", headers=headers)
        self.assert_valid_http_request_message(messages[0], body=b"")
        # It should NOT appear in the client scope item
        self.assertNotEqual(scope["client"], ["10.1.2.3", 80])

    def test_x_forwarded_for_parsed(self):
        """
        When X-Forwarded-For is enabled, make sure it is respected.
        """
        headers = [[b"X-Forwarded-For", b"10.1.2.3"], [b"X-Forwarded-Port", b"80"]]
        scope, messages = self.run_daphne_request("GET", "/", headers=headers, xff=True)
        self.assert_valid_http_scope(scope, "GET", "/", headers=headers)
        self.assert_valid_http_request_message(messages[0], body=b"")
        # It should now appear in the client scope item
        self.assertEqual(scope["client"], ["10.1.2.3", 80])

    def test_x_forwarded_for_no_port(self):
        """
        When X-Forwarded-For is enabled but only the host is passed, make sure
        that at least makes it through.
        """
        headers = [[b"X-Forwarded-For", b"10.1.2.3"]]
        scope, messages = self.run_daphne_request("GET", "/", headers=headers, xff=True)
        self.assert_valid_http_scope(scope, "GET", "/", headers=headers)
        self.assert_valid_http_request_message(messages[0], body=b"")
        # It should now appear in the client scope item
        self.assertEqual(scope["client"], ["10.1.2.3", 0])

    def test_bad_requests(self):
        """
        Tests that requests with invalid (non-ASCII) characters fail.
        """
        # Bad path
        response = self.run_daphne_raw(
            b"GET /\xc3\xa4\xc3\xb6\xc3\xbc HTTP/1.0\r\n\r\n"
        )
        self.assertTrue(response.startswith(b"HTTP/1.0 400 Bad Request"))
        # Bad querystring
        response = self.run_daphne_raw(
            b"GET /?\xc3\xa4\xc3\xb6\xc3\xbc HTTP/1.0\r\n\r\n"
        )
        self.assertTrue(response.startswith(b"HTTP/1.0 400 Bad Request"))
