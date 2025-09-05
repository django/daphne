import logging
import time
import traceback
from urllib.parse import unquote

from twisted.internet.defer import inlineCallbacks, maybeDeferred
from twisted.internet.interfaces import IProtocolNegotiationFactory
from twisted.web import http
from zope.interface import implementer

from .utils import parse_x_forwarded_for

logger = logging.getLogger(__name__)


class WebRequest(http.Request):
    """
    Request that either hands off information to channels, or offloads
    to a WebSocket class.

    Does some extra processing over the normal Twisted Web request to separate
    GET and POST out.
    """

    error_template = (
        b"<!DOCTYPE html>"
        b"<html>"
        b"<head><title>%(status)d %(status_text)s</title></head>"
        b"<body><h1>%(status)d %(status_text)s</h1>%(text)s</body>"
        b"</html>"
    )

    def __init__(self, *args, **kwargs):
        http.Request.__init__(self, *args, **kwargs)
        # Easy server link
        self.server = self.channel.factory.server
        self.application_queue = None
        self._response_started = False
        self.client_addr = None
        self.server_addr = None
        self.client_scheme = None
        # Build the client address
        if self.transport:
            peer = self.transport.getPeer()
            host = self.transport.getHost()
            # Always set scheme if we have a transport
            self.client_scheme = (
                "https" if hasattr(peer, "is_ssl") and peer.is_ssl else "http"
            )
            if hasattr(peer, "host") and hasattr(peer, "port"):
                self.client_addr = [str(peer.host), peer.port]
                self.server_addr = [str(host.host), host.port]
        # Get upgrade header
        upgrade_header = None
        if self.requestHeaders.hasHeader(b"Upgrade"):
            upgrade_header = self.requestHeaders.getRawHeaders(b"Upgrade")[0]
        self.is_websocket = upgrade_header and upgrade_header.lower() == b"websocket"
        # Hook up request parsing
        self.socket_opened = time.time()
        self.server.protocol_connected(self)

    @inlineCallbacks
    def process(self):
        """
        Called when all headers have been received and we can start processing content.
        """
        # Get upgrade header
        upgrade_header = None
        if self.requestHeaders.hasHeader(b"Upgrade"):
            upgrade_header = self.requestHeaders.getRawHeaders(b"Upgrade")[0]
        # Get client address if forwarded
        if self.server.proxy_forwarded_address_header:
            self.client_addr, self.client_scheme = parse_x_forwarded_for(
                {name: value for name, value in self.requestHeaders.getAllRawHeaders()},
                self.server.proxy_forwarded_address_header,
                self.server.proxy_forwarded_port_header,
                self.server.proxy_forwarded_proto_header,
                self.client_addr,
                self.client_scheme,
            )
        # Check for maximum request body size
        if self.server.request_max_size:
            self.channel.maxData = self.server.request_max_size
        # Get query string
        self.query_string = self.uri.split(b"?", 1)[1] if b"?" in self.uri else b""
        try:
            # Process WebSocket requests via HTTP upgrade
            if upgrade_header and upgrade_header.lower() == b"websocket":
                # Pass request to WebSocketResource for handling
                self.server.ws_resource.render_GET(self)
                # The WebSocketResource will handle the rest of the connection
                logger.debug("Upgraded connection %s to WebSocket", self.client_addr)

                # Don't continue with HTTP processing
                return
            # Handle normal HTTP requests
            else:
                # Sanitize and decode headers, potentially extracting root path
                self.clean_headers = []
                self.root_path = self.server.root_path
                for name, values in self.requestHeaders.getAllRawHeaders():
                    # Prevent CVE-2015-0219
                    if b"_" in name:
                        continue
                    for value in values:
                        if name.lower() == b"daphne-root-path":
                            self.root_path = unquote(value.decode("ascii"))
                        else:
                            self.clean_headers.append((name.lower(), value))
                logger.debug("HTTP %s request for %s", self.method, self.client_addr)
                self.content.seek(0, 0)
                # Work out the application scope and create application
                self.application_queue = yield maybeDeferred(
                    self.server.create_application,
                    self,
                    {
                        "type": "http",
                        # TODO: Correctly say if it's 1.1 or 1.0
                        "http_version": self.clientproto.split(b"/")[-1].decode(
                            "ascii"
                        ),
                        "method": self.method.decode("ascii"),
                        "path": unquote(self.path.decode("ascii")),
                        "raw_path": self.path,
                        "root_path": self.root_path,
                        "scheme": self.client_scheme,
                        "query_string": self.query_string,
                        "headers": self.clean_headers,
                        "client": self.client_addr,
                        "server": self.server_addr,
                    },
                )
                # Check they didn't close an unfinished request
                if self.application_queue is None or self.content.closed:
                    # Not much we can do, the request is prematurely abandoned.
                    return
                # Run application against request
                buffer_size = self.server.request_buffer_size
                while True:
                    chunk = self.content.read(buffer_size)
                    more_body = not (len(chunk) < buffer_size)
                    payload = {
                        "type": "http.request",
                        "body": chunk,
                        "more_body": more_body,
                    }
                    self.application_queue.put_nowait(payload)
                    if not more_body:
                        break

        except Exception:
            logger.error(traceback.format_exc())
            self.basic_error(
                500, b"Internal Server Error", "Daphne HTTP processing error"
            )

    def connectionLost(self, reason):
        """
        Cleans up reply channel on close.
        """
        if self.application_queue:
            self.send_disconnect()
        logger.debug("HTTP disconnect for %s", self.client_addr)
        http.Request.connectionLost(self, reason)
        self.server.protocol_disconnected(self)

    def finish(self):
        """
        Cleans up reply channel on close.
        """
        if self.application_queue:
            self.send_disconnect()
        logger.debug("HTTP close for %s", self.client_addr)
        http.Request.finish(self)
        self.server.protocol_disconnected(self)

    ### Server reply callbacks

    def handle_reply(self, message):
        """
        Handles a reply from the client
        """
        # Handle connections that are already closed
        if self.finished or self.channel is None:
            return
        # Check message validity
        if "type" not in message:
            raise ValueError("Message has no type defined")
        # Handle message
        if message["type"] == "http.response.start":
            if self._response_started:
                raise ValueError("HTTP response has already been started")
            self._response_started = True
            if "status" not in message:
                raise ValueError(
                    "Specifying a status code is required for a Response message."
                )
            # Set HTTP status code
            self.setResponseCode(message["status"])
            # Write headers
            for header, value in message.get("headers", {}):
                self.responseHeaders.addRawHeader(header, value)
            if self.server.server_name and not self.responseHeaders.hasHeader("server"):
                self.setHeader(b"server", self.server.server_name.encode())
            logger.debug(
                "HTTP %s response started for %s", message["status"], self.client_addr
            )
        elif message["type"] == "http.response.body":
            if not self._response_started:
                raise ValueError(
                    "HTTP response has not yet been started but got %s"
                    % message["type"]
                )
            # Write out body
            http.Request.write(self, message.get("body", b""))
            # End if there's no more content
            if not message.get("more_body", False):
                self.finish()
                logger.debug("HTTP response complete for %s", self.client_addr)
                try:
                    uri = self.uri.decode("ascii")
                except UnicodeDecodeError:
                    # The path is malformed somehow - do our best to log something
                    uri = repr(self.uri)
                try:
                    self.server.log_action(
                        "http",
                        "complete",
                        {
                            "path": uri,
                            "status": self.code,
                            "method": self.method.decode("ascii", "replace"),
                            "client": (
                                "%s:%s" % tuple(self.client_addr)
                                if self.client_addr
                                else None
                            ),
                            "time_taken": self.duration(),
                            "size": self.sentLength,
                        },
                    )
                except Exception:
                    logger.error(traceback.format_exc())
            else:
                logger.debug("HTTP response chunk for %s", self.client_addr)
        else:
            raise ValueError("Cannot handle message type %s!" % message["type"])

    def handle_exception(self, exception):
        """
        Called by the server when our application tracebacks
        """
        self.basic_error(500, b"Internal Server Error", "Exception inside application.")

    def check_timeouts(self):
        """
        Called periodically to see if we should timeout something
        """
        # Web timeout checking
        if self.server.http_timeout and self.duration() > self.server.http_timeout:
            if self._response_started:
                logger.warning("Application timed out while sending response")
                self.finish()
            else:
                self.basic_error(
                    503,
                    b"Service Unavailable",
                    "Application failed to respond within time limit.",
                )

    ### Utility functions

    def send_disconnect(self):
        """
        Sends a http.disconnect message.
        Useful only really for long-polling.
        """
        # If we don't yet have a path, then don't send as we never opened.
        if self.path:
            self.application_queue.put_nowait({"type": "http.disconnect"})

    def duration(self):
        """
        Returns the time since the start of the request.
        """
        if not hasattr(self, "socket_opened"):
            return 0
        return time.time() - self.socket_opened

    def basic_error(self, status, status_text, body):
        """
        Responds with a server-level error page (very basic)
        """
        self.handle_reply(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"Content-Type", b"text/html; charset=utf-8")],
            }
        )
        self.handle_reply(
            {
                "type": "http.response.body",
                "body": self.error_template
                % {
                    "status": status,
                    "status_text": status_text,
                    "text": body,
                },
            }
        )

    def __hash__(self):
        return hash(id(self))

    def __eq__(self, other):
        return id(self) == id(other)


@implementer(IProtocolNegotiationFactory)
class HTTPFactory(http.HTTPFactory):
    """
    Factory which takes care of tracking which protocol
    instances or request instances are responsible for which
    named response channels, so incoming messages can be
    routed appropriately.
    """

    def __init__(self, server):
        http.HTTPFactory.__init__(self)
        self.server = server

    def buildProtocol(self, addr):
        """
        Builds protocol instances. This override is used to ensure we use our
        own Request object instead of the default.
        """
        try:
            protocol = http.HTTPFactory.buildProtocol(self, addr)
            protocol.requestFactory = WebRequest
            return protocol
        except Exception:
            logger.error("Cannot build protocol: %s" % traceback.format_exc())
            raise

    # IProtocolNegotiationFactory
    def acceptableProtocols(self):
        """
        Protocols this server can speak after ALPN negotiation. Currently that
        is HTTP/1.1 and optionally HTTP/2. Websockets cannot be negotiated
        using ALPN, so that doesn't go here: anyone wanting websockets will
        negotiate HTTP/1.1 and then do the upgrade dance.
        """
        baseProtocols = [b"http/1.1"]

        if http.H2_ENABLED:
            baseProtocols.insert(0, b"h2")

        return baseProtocols
