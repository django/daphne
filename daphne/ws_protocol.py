import logging
import time
import traceback
from urllib.parse import unquote

from autobahn.twisted.websocket import (
    ConnectionDeny,
    WebSocketServerFactory,
    WebSocketServerProtocol,
)
from twisted.internet import defer

from .utils import parse_x_forwarded_for

logger = logging.getLogger(__name__)


class WebSocketProtocol(WebSocketServerProtocol):
    """
    Protocol which supports WebSockets and forwards incoming messages to
    the websocket channels.
    """

    application_type = "websocket"

    # If we should send no more messages (e.g. we error-closed the socket)
    muted = False

    def onConnect(self, request):
        self.server = self.factory.server_class
        self.server.protocol_connected(self)
        self.request = request
        self.protocol_to_accept = None
        self.root_path = self.server.root_path
        self.socket_opened = time.time()
        self.last_ping = time.time()
        try:
            # Sanitize and decode headers, potentially extracting root path
            self.clean_headers = []
            for name, value in request.headers.items():
                name = name.encode("ascii")
                # Prevent CVE-2015-0219
                if b"_" in name:
                    continue
                if name.lower() == b"daphne-root-path":
                    self.root_path = unquote(value)
                else:
                    self.clean_headers.append((name.lower(), value.encode("latin1")))
            # Get client address if possible
            peer = self.transport.getPeer()
            host = self.transport.getHost()
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
            # Make new application instance with scope
            self.path = request.path.encode("ascii")
            self.application_deferred = defer.maybeDeferred(
                self.server.create_application,
                self,
                {
                    "type": "websocket",
                    "path": unquote(self.path.decode("ascii")),
                    "raw_path": self.path,
                    "root_path": self.root_path,
                    "headers": self.clean_headers,
                    "query_string": self._raw_query_string,  # Passed by HTTP protocol
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

        # Make a deferred and return it - we'll either call it or err it later on
        self.handshake_deferred = defer.Deferred()
        return self.handshake_deferred

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
                "path": self.request.path,
                "client": "%s:%s" % tuple(self.client_addr)
                if self.client_addr
                else None,
            },
        )

    def applicationCreateFailed(self, failure):
        """
        Called when application creation fails.
        """
        logger.error(failure)
        return failure

    ### Twisted event handling

    def onOpen(self):
        # Send news that this channel is open
        logger.debug("WebSocket %s open and established", self.client_addr)
        self.server.log_action(
            "websocket",
            "connected",
            {
                "path": self.request.path,
                "client": "%s:%s" % tuple(self.client_addr)
                if self.client_addr
                else None,
            },
        )

    def onMessage(self, payload, isBinary):
        # If we're muted, do nothing.
        if self.muted:
            logger.debug("Muting incoming frame on %s", self.client_addr)
            return
        logger.debug("WebSocket incoming frame on %s", self.client_addr)
        self.last_ping = time.time()
        if isBinary:
            self.application_queue.put_nowait(
                {"type": "websocket.receive", "bytes": payload}
            )
        else:
            self.application_queue.put_nowait(
                {"type": "websocket.receive", "text": payload.decode("utf8")}
            )

    def onClose(self, wasClean, code, reason):
        """
        Called when Twisted closes the socket.
        """
        self.server.protocol_disconnected(self)
        logger.debug("WebSocket closed for %s", self.client_addr)
        if not self.muted and hasattr(self, "application_queue"):
            self.application_queue.put_nowait(
                {"type": "websocket.disconnect", "code": code}
            )
        self.server.log_action(
            "websocket",
            "disconnected",
            {
                "path": self.request.path,
                "client": "%s:%s" % tuple(self.client_addr)
                if self.client_addr
                else None,
            },
        )

    ### Internal event handling

    def handle_reply(self, message):
        if "type" not in message:
            raise ValueError("Message has no type defined")
        if message["type"] == "websocket.accept":
            self.serverAccept(message.get("subprotocol", None))
        elif message["type"] == "websocket.close":
            if self.state == self.STATE_CONNECTING:
                self.serverReject()
            else:
                self.serverClose(code=message.get("code", None))
        elif message["type"] == "websocket.send":
            if self.state == self.STATE_CONNECTING:
                raise ValueError("Socket has not been accepted, so cannot send over it")
            if message.get("bytes", None) and message.get("text", None):
                raise ValueError(
                    "Got invalid WebSocket reply message on %s - contains both bytes and text keys"
                    % (message,)
                )
            if message.get("bytes", None):
                self.serverSend(message["bytes"], True)
            if message.get("text", None):
                self.serverSend(message["text"], False)

    def handle_exception(self, exception):
        """
        Called by the server when our application tracebacks
        """
        if hasattr(self, "handshake_deferred"):
            # If the handshake is still ongoing, we need to emit a HTTP error
            # code rather than a WebSocket one.
            self.handshake_deferred.errback(
                ConnectionDeny(code=500, reason="Internal server error")
            )
        else:
            self.sendCloseFrame(code=1011)

    def serverAccept(self, subprotocol=None):
        """
        Called when we get a message saying to accept the connection.
        """
        self.handshake_deferred.callback(subprotocol)
        del self.handshake_deferred
        logger.debug("WebSocket %s accepted by application", self.client_addr)

    def serverReject(self):
        """
        Called when we get a message saying to reject the connection.
        """
        self.handshake_deferred.errback(
            ConnectionDeny(code=403, reason="Access denied")
        )
        del self.handshake_deferred
        self.server.protocol_disconnected(self)
        logger.debug("WebSocket %s rejected by application", self.client_addr)
        self.server.log_action(
            "websocket",
            "rejected",
            {
                "path": self.request.path,
                "client": "%s:%s" % tuple(self.client_addr)
                if self.client_addr
                else None,
            },
        )

    def serverSend(self, content, binary=False):
        """
        Server-side channel message to send a message.
        """
        if self.state == self.STATE_CONNECTING:
            self.serverAccept()
        logger.debug("Sent WebSocket packet to client for %s", self.client_addr)
        if binary:
            self.sendMessage(content, binary)
        else:
            self.sendMessage(content.encode("utf8"), binary)

    def serverClose(self, code=None):
        """
        Server-side channel message to close the socket
        """
        code = 1000 if code is None else code
        self.sendClose(code=code)

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
            self.serverClose()
        # Ping check
        # If we're still connecting, deny the connection
        if self.state == self.STATE_CONNECTING:
            if self.duration() > self.server.websocket_connect_timeout:
                self.serverReject()
        elif self.state == self.STATE_OPEN:
            if (time.time() - self.last_ping) > self.server.ping_interval:
                self._sendAutoPing()
                self.last_ping = time.time()

    def __hash__(self):
        return hash(id(self))

    def __eq__(self, other):
        return id(self) == id(other)

    def __repr__(self):
        return f"<WebSocketProtocol client={self.client_addr!r} path={self.path!r}>"


class WebSocketFactory(WebSocketServerFactory):
    """
    Factory subclass that remembers what the "main"
    factory is, so WebSocket protocols can access it
    to get reply ID info.
    """

    protocol = WebSocketProtocol

    def __init__(self, server_class, *args, **kwargs):
        self.server_class = server_class
        WebSocketServerFactory.__init__(self, *args, **kwargs)

    def buildProtocol(self, addr):
        """
        Builds protocol instances. We use this to inject the factory object into the protocol.
        """
        try:
            protocol = super().buildProtocol(addr)
            protocol.factory = self
            return protocol
        except Exception:
            logger.error("Cannot build protocol: %s" % traceback.format_exc())
            raise
