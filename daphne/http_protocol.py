import logging
import time
import traceback
from urllib.parse import unquote

from twisted.internet.defer import inlineCallbacks, maybeDeferred
from twisted.internet.interfaces import IProtocolNegotiationFactory
from twisted.protocols.policies import ProtocolWrapper
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
        """
        <html>
            <head>
                <title>%(title)s</title>
                <style>
                    body { font-family: sans-serif; margin: 0; padding: 0; }
                    h1 { padding: 0.6em 0 0.2em 20px; color: #896868; margin: 0; }
                    p { padding: 0 0 0.3em 20px; margin: 0; }
                    footer { padding: 1em 0 0.3em 20px; color: #999; font-size: 80%%; font-style: italic; }
                </style>
            </head>
            <body>
                <h1>%(title)s</h1>
                <p>%(body)s</p>
                <footer>Daphne</footer>
            </body>
        </html>
    """.replace(
            "\n", ""
        )
        .replace("    ", " ")
        .replace("   ", " ")
        .replace("  ", " ")
    )  # Shorten it a bit, bytes wise

    def __init__(self, *args, **kwargs):
        self.client_addr = None
        self.server_addr = None
        try:
            http.Request.__init__(self, *args, **kwargs)
            # Easy server link
            self.server = self.channel.factory.server
            self.application_queue = None
            self._response_started = False
            self.server.protocol_connected(self)
        except Exception:
            logger.error(traceback.format_exc())
            raise

    ### Twisted progress callbacks

    @inlineCallbacks
    def process(self):
        try:
            self.request_start = time.time()
            # Get upgrade header
            upgrade_header = None
            if self.requestHeaders.hasHeader(b"Upgrade"):
                upgrade_header = self.requestHeaders.getRawHeaders(b"Upgrade")[0]
            # Get client address if possible
            if hasattr(self.client, "host") and hasattr(self.client, "port"):
                # client.host and host.host are byte strings in Python 2, but spec
                # requires unicode string.
                self.client_addr = [str(self.client.host), self.client.port]
                self.server_addr = [str(self.host.host), self.host.port]

            self.client_scheme = "https" if self.isSecure() else "http"

            # See if we need to get the address from a proxy header instead
            if self.server.proxy_forwarded_address_header:
                self.client_addr, self.client_scheme = parse_x_forwarded_for(
                    self.requestHeaders,
                    self.server.proxy_forwarded_address_header,
                    self.server.proxy_forwarded_port_header,
                    self.server.proxy_forwarded_proto_header,
                    self.client_addr,
                    self.client_scheme,
                )
            # Check for unicodeish path (or it'll crash when trying to parse)
            try:
                self.path.decode("ascii")
            except UnicodeDecodeError:
                self.path = b"/"
                self.basic_error(400, b"Bad Request", "Invalid characters in path")
                return
            # Calculate query string
            self.query_string = b""
            if b"?" in self.uri:
                self.query_string = self.uri.split(b"?", 1)[1]
                try:
                    self.query_string.decode("ascii")
                except UnicodeDecodeError:
                    self.basic_error(400, b"Bad Request", "Invalid query string")
                    return
            # Is it WebSocket? IS IT?!
            if upgrade_header and upgrade_header.lower() == b"websocket":
                # Make WebSocket protocol to hand off to
                protocol = self.server.ws_factory.buildProtocol(
                    self.transport.getPeer()
                )
                if not protocol:
                    # If protocol creation fails, we signal "internal server error"
                    self.setResponseCode(500)
                    logger.warn("Could not make WebSocket protocol")
                    self.finish()
                # Give it the raw query string
                protocol._raw_query_string = self.query_string
                # Port across transport
                transport, self.transport = self.transport, None
                if isinstance(transport, ProtocolWrapper):
                    # i.e. TLS is a wrapping protocol
                    transport.wrappedProtocol = protocol
                else:
                    transport.protocol = protocol
                protocol.makeConnection(transport)
                # Re-inject request
                data = self.method + b" " + self.uri + b" HTTP/1.1\x0d\x0a"
                for h in self.requestHeaders.getAllRawHeaders():
                    data += h[0] + b": " + b",".join(h[1]) + b"\x0d\x0a"
                data += b"\x0d\x0a"
                data += self.content.read()
                protocol.dataReceived(data)
                # Remove our HTTP reply channel association
                logger.debug("Upgraded connection %s to WebSocket", self.client_addr)
                self.server.protocol_disconnected(self)
                # Resume the producer so we keep getting data, if it's available as a method
                self.channel._networkProducer.resumeProducing()

            # Boring old HTTP.
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
                            "client": "%s:%s" % tuple(self.client_addr)
                            if self.client_addr
                            else None,
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
        if not hasattr(self, "request_start"):
            return 0
        return time.time() - self.request_start

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
                "body": (
                    self.error_template
                    % {
                        "title": str(status) + " " + status_text.decode("ascii"),
                        "body": body,
                    }
                ).encode("utf8"),
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
