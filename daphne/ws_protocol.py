import logging
import time
import traceback
from urllib.parse import unquote

from twisted.internet import defer
from twisted.web.websocket import WebSocketProtocol as TwistedWebSocketProtocol

from .utils import parse_x_forwarded_for

logger = logging.getLogger(__name__)


class WebSocketProtocol(TwistedWebSocketProtocol):
    """
    Protocol which supports WebSockets and forwards incoming messages to
    the websocket channels.
    """

    application_type = "websocket"

    # If we should send no more messages (e.g. we error-closed the socket)
    muted = False

    def __init__(self, factory, request):
        self.factory = factory
        self.transport = None
        self.server = self.factory.server_class
        self.socket_opened = time.time()
        self.last_ping = time.time()
        self.client_addr = None
        self.server_addr = None
        self.clean_headers = []
        self.handshake_deferred = None
        self.path = None
        self.root_path = None
        self.application_queue = None
        self.request = request

    def negotiationStarted(self, transport):
        """
        Called when the WebSocket negotiation starts.
        """
        self.transport = transport
        self.server.protocol_connected(self)
        self.protocol_to_accept = None
        self.root_path = self.server.root_path
        try:
            # Sanitize and decode headers, potentially extracting root path
            self.clean_headers = []
            for name, value in self.request.requestHeaders.getAllRawHeaders():
                name = name.lower()
                # Prevent CVE-2015-0219
                if b"_" in name:
                    continue
                if name == b"daphne-root-path":
                    self.root_path = unquote(value[0].decode("ascii"))
                else:
                    self.clean_headers.append((name, value[0]))

            # Get client address if possible
            # The transport is a _WebSocketWireProtocol, we need the underlying transport
            underlying_transport = getattr(self.transport, "transport", self.transport)
            peer = underlying_transport.getPeer()
            host = underlying_transport.getHost()
            if hasattr(peer, "host") and hasattr(peer, "port"):
                self.client_addr = [str(peer.host), peer.port]
                self.server_addr = [str(host.host), host.port]
            else:
                self.client_addr = None
                self.server_addr = None

            if self.server.proxy_forwarded_address_header:
                self.client_addr, self.client_scheme = parse_x_forwarded_for(
                    dict(self.clean_headers),
                    self.server.proxy_forwarded_address_header,
                    self.server.proxy_forwarded_port_header,
                    self.server.proxy_forwarded_proto_header,
                    self.client_addr,
                )

            # Decode websocket subprotocol options
            subprotocols = []
            for header, value in self.clean_headers:
                if header == b"sec-websocket-protocol":
                    subprotocols = [
                        x.strip() for x in unquote(value.decode("ascii")).split(",")
                    ]

            # Extract query string
            query_string = b""
            if b"?" in self.request.uri:
                query_string = self.request.uri.split(b"?", 1)[1]

            # Get the path
            self.path = self.request.path

            # Make new application instance with scope
            self.application_deferred = defer.maybeDeferred(
                self.server.create_application,
                self,
                {
                    "type": "websocket",
                    "path": unquote(self.path.decode("ascii")),
                    "raw_path": self.path,
                    "root_path": self.root_path,
                    "headers": self.clean_headers,
                    "query_string": query_string,
                    "client": self.client_addr,
                    "server": self.server_addr,
                    "subprotocols": subprotocols,
                },
            )
            if self.application_deferred is not None:
                self.application_deferred.addCallback(self.applicationCreateWorked)
                self.application_deferred.addErrback(self.applicationCreateFailed)
        except Exception:
            # Exceptions here are not displayed right, just 500.
            # Turn them into an ERROR log.
            logger.error(traceback.format_exc())
            raise

    def applicationCreateWorked(self, application_queue):
        """
        Called when the background thread has successfully made the application
        instance.
        """
        # Store the application's queue
        self.application_queue = application_queue
        # Send over the connect message
        self.application_queue.put_nowait({"type": "websocket.connect"})
        self.server.log_action(
            "websocket",
            "connecting",
            {
                "path": self.request.path.decode("ascii"),
                "client": (
                    "%s:%s" % tuple(self.client_addr) if self.client_addr else None
                ),
            },
        )

    def applicationCreateFailed(self, failure):
        """
        Called when application creation fails.
        """
        logger.error(failure)
        return failure

    def negotiationFinished(self):
        """
        Called when the WebSocket negotiation is finished.
        """
        logger.debug("WebSocket %s open and established", self.client_addr)
        self.server.log_action(
            "websocket",
            "connected",
            {
                "path": self.request.path.decode("ascii"),
                "client": (
                    "%s:%s" % tuple(self.client_addr) if self.client_addr else None
                ),
            },
        )

    def textMessageReceived(self, message):
        """
        Called when a text message is received.
        """
        # If we're muted, do nothing.
        if self.muted:
            logger.debug("Muting incoming frame on %s", self.client_addr)
            return
        logger.debug("WebSocket incoming frame on %s", self.client_addr)
        self.last_ping = time.time()
        self.application_queue.put_nowait(
            {"type": "websocket.receive", "text": message}
        )

    def bytesMessageReceived(self, data):
        """
        Called when a binary message is received.
        """
        # If we're muted, do nothing.
        if self.muted:
            logger.debug("Muting incoming frame on %s", self.client_addr)
            return
        logger.debug("WebSocket incoming frame on %s", self.client_addr)
        self.last_ping = time.time()
        self.application_queue.put_nowait({"type": "websocket.receive", "bytes": data})

    def connectionLost(self, reason):
        """
        Called when the WebSocket connection is lost.
        """
        self.server.protocol_disconnected(self)
        logger.debug("WebSocket closed for %s", self.client_addr)
        if not self.muted and hasattr(self, "application_queue"):
            self.application_queue.put_nowait(
                {"type": "websocket.disconnect", "code": 1000}  # Default close code
            )
        self.server.log_action(
            "websocket",
            "disconnected",
            {
                "path": self.request.path.decode("ascii"),
                "client": (
                    "%s:%s" % tuple(self.client_addr) if self.client_addr else None
                ),
            },
        )

    def pongReceived(self, payload):
        """
        Called when a pong frame is received in response to a ping.
        """
        self.last_ping = time.time()

    ### Internal event handling

    def handle_reply(self, message):
        """
        Handle reply messages from the application.
        """
        if "type" not in message:
            raise ValueError("Message has no type defined")

        if message["type"] == "websocket.accept":
            # Accept is handled by WebSocketResource in Twisted 25
            # Our protocol is already established at this point
            pass
        elif message["type"] == "websocket.close":
            self.transport.loseConnection(code=message.get("code", 1000))
        elif message["type"] == "websocket.send":
            if message.get("bytes", None) and message.get("text", None):
                raise ValueError(
                    "Got invalid WebSocket reply message on %s - contains both bytes and text keys"
                    % (self.client_addr,)
                )
            if message.get("bytes", None):
                self.transport.sendBytesMessage(message["bytes"])
            if message.get("text", None):
                self.transport.sendTextMessage(message["text"])

    def handle_exception(self, exception):
        """
        Called by the server when our application tracebacks
        """
        # In the new Twisted WebSocket implementation, we can just close the connection
        self.transport.loseConnection(code=1011)  # Internal server error

    ### Utils

    def duration(self):
        """
        Returns the time since the socket was opened
        """
        return time.time() - self.socket_opened

    def check_timeouts(self):
        """
        Called periodically to see if we should timeout something
        """
        # Web timeout checking
        if (
            self.duration() > self.server.websocket_timeout
            and self.server.websocket_timeout >= 0
        ):
            self.transport.loseConnection(code=1000)

        # Ping check
        if hasattr(self, "transport") and self.transport:
            if (time.time() - self.last_ping) > self.server.ping_interval:
                self.transport.ping()
                self.last_ping = time.time()

    def __hash__(self):
        return hash(id(self))

    def __eq__(self, other):
        return id(self) == id(other)

    def __repr__(self):
        return f"<WebSocketProtocol client={self.client_addr!r} path={self.path!r}>"


class WebSocketFactory:
    """
    Factory for WebSocket protocols.
    """

    def __init__(self, server_class):
        self.server_class = server_class

    def buildProtocol(self, request):
        """
        Builds a new WebSocket protocol.
        """
        try:
            protocol = WebSocketProtocol(self, request)
            return protocol
        except Exception:
            logger.error("Cannot build protocol: %s" % traceback.format_exc())
            raise
