import collections
import time
from urllib import parse

import http_strategies
from http_base import DaphneTestCase, DaphneTestingInstance
from hypothesis import given, settings

from daphne.testing import BaseDaphneTestingInstance


class TestWebsocket(DaphneTestCase):
    """
    Tests WebSocket handshake, send and receive.
    """

    def assert_valid_websocket_scope(
        self, scope, path="/", params=None, headers=None, scheme=None, subprotocols=None
    ):
        """
        Checks that the passed scope is a valid ASGI HTTP scope regarding types
        and some urlencoding things.
        """
        # Check overall keys
        self.assert_key_sets(
            required_keys={
                "asgi",
                "type",
                "path",
                "raw_path",
                "query_string",
                "headers",
            },
            optional_keys={"scheme", "root_path", "client", "server", "subprotocols"},
            actual_keys=scope.keys(),
        )
        self.assertEqual(scope["asgi"]["version"], "3.0")
        # Check that it is the right type
        self.assertEqual(scope["type"], "websocket")
        # Path
        self.assert_valid_path(scope["path"])
        # Scheme
        self.assertIn(scope.get("scheme", "ws"), ["ws", "wss"])
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
            transformed_scope_headers.setdefault(name, [])
            # Make sure to split out any headers collapsed with commas
            for bit in value.split(b","):
                if bit.strip():
                    transformed_scope_headers[name].append(bit.strip())
        transformed_request_headers = collections.defaultdict(list)
        for name, value in headers or []:
            expected_name = name.lower().strip()
            expected_value = value.strip()
            # Make sure to split out any headers collapsed with commas
            transformed_request_headers.setdefault(expected_name, [])
            for bit in expected_value.split(b","):
                if bit.strip():
                    transformed_request_headers[expected_name].append(bit.strip())
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
        # Subprotocols
        scope_subprotocols = scope.get("subprotocols", [])
        if scope_subprotocols:
            assert all(isinstance(x, str) for x in scope_subprotocols)
        if subprotocols:
            assert sorted(scope_subprotocols) == sorted(subprotocols)

    def assert_valid_websocket_connect_message(self, message):
        """
        Asserts that a message is a valid http.request message
        """
        # Check overall keys
        self.assert_key_sets(
            required_keys={"type"}, optional_keys=set(), actual_keys=message.keys()
        )
        # Check that it is the right type
        self.assertEqual(message["type"], "websocket.connect")

    def test_accept(self):
        """
        Tests we can open and accept a socket.
        """
        with DaphneTestingInstance() as test_app:
            test_app.add_send_messages([{"type": "websocket.accept"}])
            self.websocket_handshake(test_app)
            # Validate the scope and messages we got
            scope, messages = test_app.get_received()
            self.assert_valid_websocket_scope(scope)
            self.assert_valid_websocket_connect_message(messages[0])

    def test_reject(self):
        """
        Tests we can reject a socket and it won't complete the handshake.
        """
        with DaphneTestingInstance() as test_app:
            test_app.add_send_messages([{"type": "websocket.close"}])
            with self.assertRaises(RuntimeError):
                self.websocket_handshake(test_app)

    def test_subprotocols(self):
        """
        Tests that we can ask for subprotocols and then select one.
        """
        subprotocols = ["proto1", "proto2"]
        with DaphneTestingInstance() as test_app:
            test_app.add_send_messages(
                [{"type": "websocket.accept", "subprotocol": "proto2"}]
            )
            _, subprotocol = self.websocket_handshake(
                test_app, subprotocols=subprotocols
            )
            # Validate the scope and messages we got
            assert subprotocol == "proto2"
            scope, messages = test_app.get_received()
            self.assert_valid_websocket_scope(scope, subprotocols=subprotocols)
            self.assert_valid_websocket_connect_message(messages[0])

    def test_xff(self):
        """
        Tests that X-Forwarded-For headers get parsed right
        """
        headers = [["X-Forwarded-For", "10.1.2.3"], ["X-Forwarded-Port", "80"]]
        with DaphneTestingInstance(xff=True) as test_app:
            test_app.add_send_messages([{"type": "websocket.accept"}])
            self.websocket_handshake(test_app, headers=headers)
            # Validate the scope and messages we got
            scope, messages = test_app.get_received()
            self.assert_valid_websocket_scope(scope)
            self.assert_valid_websocket_connect_message(messages[0])
            assert scope["client"] == ["10.1.2.3", 80]

    @given(
        request_path=http_strategies.http_path(),
        request_params=http_strategies.query_params(),
        request_headers=http_strategies.headers(),
    )
    @settings(max_examples=5, deadline=2000)
    def test_http_bits(self, request_path, request_params, request_headers):
        """
        Tests that various HTTP-level bits (query string params, path, headers)
        carry over into the scope.
        """
        with DaphneTestingInstance() as test_app:
            test_app.add_send_messages([{"type": "websocket.accept"}])
            self.websocket_handshake(
                test_app,
                path=parse.quote(request_path),
                params=request_params,
                headers=request_headers,
            )
            # Validate the scope and messages we got
            scope, messages = test_app.get_received()
            self.assert_valid_websocket_scope(
                scope, path=request_path, params=request_params, headers=request_headers
            )
            self.assert_valid_websocket_connect_message(messages[0])

    def test_raw_path(self):
        """
        Tests that /foo%2Fbar produces raw_path and a decoded path
        """
        with DaphneTestingInstance() as test_app:
            test_app.add_send_messages([{"type": "websocket.accept"}])
            self.websocket_handshake(test_app, path="/foo%2Fbar")
            # Validate the scope and messages we got
            scope, _ = test_app.get_received()

        self.assertEqual(scope["path"], "/foo/bar")
        self.assertEqual(scope["raw_path"], b"/foo%2Fbar")

    @given(daphne_path=http_strategies.http_path())
    @settings(max_examples=5, deadline=2000)
    def test_root_path(self, *, daphne_path):
        """
        Tests root_path handling.
        """
        headers = [("Daphne-Root-Path", parse.quote(daphne_path))]
        with DaphneTestingInstance() as test_app:
            test_app.add_send_messages([{"type": "websocket.accept"}])
            self.websocket_handshake(
                test_app,
                path="/",
                headers=headers,
            )
            # Validate the scope and messages we got
            scope, _ = test_app.get_received()

        # Daphne-Root-Path is not included in the returned 'headers' section.
        self.assertNotIn(
            "daphne-root-path", (header[0].lower() for header in scope["headers"])
        )
        # And what we're looking for, root_path being set.
        self.assertEqual(scope["root_path"], daphne_path)

    def test_text_frames(self):
        """
        Tests we can send and receive text frames.
        """
        with DaphneTestingInstance() as test_app:
            # Connect
            test_app.add_send_messages([{"type": "websocket.accept"}])
            sock, _ = self.websocket_handshake(test_app)
            _, messages = test_app.get_received()
            self.assert_valid_websocket_connect_message(messages[0])
            # Prep frame for it to send
            test_app.add_send_messages(
                [{"type": "websocket.send", "text": "here be dragons 🐉"}]
            )
            # Send it a frame
            self.websocket_send_frame(sock, "what is here? 🌍")
            # Receive a frame and make sure it's correct
            assert self.websocket_receive_frame(sock) == "here be dragons 🐉"
            # Make sure it got our frame
            _, messages = test_app.get_received()
            assert messages[1] == {
                "type": "websocket.receive",
                "text": "what is here? 🌍",
            }

    def test_binary_frames(self):
        """
        Tests we can send and receive binary frames with things that are very
        much not valid UTF-8.
        """
        with DaphneTestingInstance() as test_app:
            # Connect
            test_app.add_send_messages([{"type": "websocket.accept"}])
            sock, _ = self.websocket_handshake(test_app)
            _, messages = test_app.get_received()
            self.assert_valid_websocket_connect_message(messages[0])
            # Prep frame for it to send
            test_app.add_send_messages(
                [{"type": "websocket.send", "bytes": b"here be \xe2 bytes"}]
            )
            # Send it a frame
            self.websocket_send_frame(sock, b"what is here? \xe2")
            # Receive a frame and make sure it's correct
            assert self.websocket_receive_frame(sock) == b"here be \xe2 bytes"
            # Make sure it got our frame
            _, messages = test_app.get_received()
            assert messages[1] == {
                "type": "websocket.receive",
                "bytes": b"what is here? \xe2",
            }

    def assert_oversized_frame_rejected(self, test_app):
        """
        Sends a 16-byte text frame and asserts the application sees only
        connect + disconnect — i.e. autobahn dropped the connection (its
        default failByDrop behaviour) before dispatching the payload.
        """
        test_app.add_send_messages([{"type": "websocket.accept"}])
        sock, _ = self.websocket_handshake(test_app)
        _, messages = test_app.get_received()
        self.assert_valid_websocket_connect_message(messages[0])
        self.websocket_send_frame(sock, "x" * 16)
        deadline = time.time() + 2
        final_messages = []
        while time.time() < deadline:
            _, final_messages = test_app.get_received()
            if any(m["type"] == "websocket.disconnect" for m in final_messages):
                break
            time.sleep(0.05)
        try:
            sock.close()
        except OSError:
            pass
        types = [m["type"] for m in final_messages]
        self.assertEqual(
            types,
            ["websocket.connect", "websocket.disconnect"],
            "Oversized frame should not have been delivered to the "
            f"application, but got: {types}",
        )

    def test_websocket_max_message_size(self):
        """
        Tests that an incoming WebSocket message exceeding
        ``websocket_max_message_size`` is rejected by autobahn before it
        reaches the application.
        """
        # 16-byte frame > 8-byte message limit.
        with DaphneTestingInstance(websocket_max_message_size=8) as test_app:
            self.assert_oversized_frame_rejected(test_app)

    def test_websocket_max_frame_size(self):
        """
        Tests that an incoming WebSocket frame exceeding
        ``websocket_max_frame_size`` is rejected by autobahn before it
        reaches the application, independently of the message size limit.
        """
        # Large message limit, so the frame size limit is what trips.
        with DaphneTestingInstance(
            websocket_max_frame_size=8,
            websocket_max_message_size=1024 * 1024,
        ) as test_app:
            self.assert_oversized_frame_rejected(test_app)

    def test_websocket_max_message_size_allows_under_limit(self):
        """
        Tests that messages under ``websocket_max_message_size`` are
        delivered to the application unchanged.
        """
        with DaphneTestingInstance(websocket_max_message_size=64) as test_app:
            test_app.add_send_messages([{"type": "websocket.accept"}])
            sock, _ = self.websocket_handshake(test_app)
            _, messages = test_app.get_received()
            self.assert_valid_websocket_connect_message(messages[0])
            test_app.add_send_messages([{"type": "websocket.send", "text": "ack"}])
            self.websocket_send_frame(sock, "x" * 16)
            assert self.websocket_receive_frame(sock) == "ack"

    def test_http_timeout(self):
        """
        Tests that the HTTP timeout doesn't kick in for WebSockets
        """
        with DaphneTestingInstance(http_timeout=1) as test_app:
            # Connect
            test_app.add_send_messages([{"type": "websocket.accept"}])
            sock, _ = self.websocket_handshake(test_app)
            _, messages = test_app.get_received()
            self.assert_valid_websocket_connect_message(messages[0])
            # Wait 2 seconds
            time.sleep(2)
            # Prep frame for it to send
            test_app.add_send_messages([{"type": "websocket.send", "text": "cake"}])
            # Send it a frame
            self.websocket_send_frame(sock, "still alive?")
            # Receive a frame and make sure it's correct
            assert self.websocket_receive_frame(sock) == "cake"

    def test_application_checker_handles_asyncio_cancellederror(self):
        with CancellingTestingInstance() as app:
            # Connect to the websocket app, it will immediately raise
            # asyncio.CancelledError
            sock, _ = self.websocket_handshake(app)
            # Disconnect from the socket
            sock.close()
            # Wait for application_checker to clean up the applications for
            # disconnected clients, and for the server to be stopped.
            time.sleep(3)
            # Make sure we received either no error, or a ConnectionsNotEmpty
            while not app.process.errors.empty():
                err, _tb = app.process.errors.get()
                if not isinstance(err, ConnectionsNotEmpty):
                    raise err
                self.fail(
                    "Server connections were not cleaned up after an asyncio.CancelledError was raised"
                )


class TestHeaderValueInjection(DaphneTestCase):
    """
    Twisted's bytes HTTP parser does not treat \\x0b, \\x0c, \\x1c, \\x1d, \\x1e
    or \\x85 as line separators, but autobahn's WebSocket handshake parser
    decodes to str and calls splitlines(), which does. Without rejection at
    the Daphne edge, an attacker can smuggle additional headers into the
    WebSocket ASGI scope through a single header value. Reject these bytes
    on both paths so values can never reach a downstream str-based parser.
    """

    INVALID_BYTES = (
        b"\x0b",  # vertical tab
        b"\x0c",  # form feed
        b"\x1c",  # file separator
        b"\x1d",  # group separator
        b"\x1e",  # record separator
        b"\x85",  # NEL
    )

    def _websocket_upgrade_request(self, value):
        return (
            b"GET /ws HTTP/1.1\r\n"
            b"Host: example.com\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"X-Padding: " + value + b"\r\n"
            b"\r\n"
        )

    def _http_request(self, value):
        return (
            b"GET / HTTP/1.1\r\n"
            b"Host: example.com\r\n"
            b"X-Padding: " + value + b"\r\n"
            b"\r\n"
        )

    def test_websocket_upgrade_rejects_smuggled_headers(self):
        for byte in self.INVALID_BYTES:
            with self.subTest(byte=byte):
                value = b"innocent" + byte + b"X-Secret-Auth: admin-token"
                response = self.run_daphne_raw(self._websocket_upgrade_request(value))
                self.assertTrue(
                    response.startswith(b"HTTP/1.1 400"),
                    f"expected 400 for byte {byte!r}, got {response[:80]!r}",
                )
                # Confirm the smuggled header didn't slip past validation.
                self.assertNotIn(b"X-Secret-Auth", response)

    def test_http_request_rejects_invalid_header_value_bytes(self):
        for byte in self.INVALID_BYTES:
            with self.subTest(byte=byte):
                value = b"innocent" + byte + b"injected"
                response = self.run_daphne_raw(self._http_request(value))
                self.assertTrue(
                    response.startswith(b"HTTP/1.1 400"),
                    f"expected 400 for byte {byte!r}, got {response[:80]!r}",
                )


async def cancelling_application(scope, receive, send):
    import asyncio

    from twisted.internet import reactor

    # Stop the server after a short delay so that the teardown is run.
    reactor.callLater(2, reactor.stop)
    await send({"type": "websocket.accept"})
    raise asyncio.CancelledError()


class ConnectionsNotEmpty(Exception):
    pass


class CancellingTestingInstance(BaseDaphneTestingInstance):
    def __init__(self):
        super().__init__(application=cancelling_application)

    def process_teardown(self):
        import multiprocessing

        # Get a hold of the enclosing DaphneProcess (we're currently running in
        # the same process as the application).
        proc = multiprocessing.current_process()
        # By now the (only) socket should have disconnected, and the
        # application_checker should have run. If there are any connections
        # still, it means that the application_checker did not clean them up.
        if proc.server.connections:
            raise ConnectionsNotEmpty()
