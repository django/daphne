from __future__ import unicode_literals

import logging
import six
import time
import traceback
from six.moves.urllib_parse import unquote, urlencode
from twisted.internet import defer

from autobahn.twisted.websocket import WebSocketServerProtocol, WebSocketServerFactory, ConnectionDeny

from .utils import parse_x_forwarded_for

logger = logging.getLogger(__name__)


class WebSocketProtocol(WebSocketServerProtocol):
    """
    Protocol which supports WebSockets and forwards incoming messages to
    the websocket channels.
    """

    # If we should send no more messages (e.g. we error-closed the socket)
    muted = False

    def set_main_factory(self, main_factory):
        self.main_factory = main_factory
        self.channel_layer = self.main_factory.channel_layer

    def onConnect(self, request):
        self.request = request
        self.packets_received = 0
        self.protocol_to_accept = None
        self.socket_opened = time.time()
        self.last_data = time.time()
        try:
            # Sanitize and decode headers
            self.clean_headers = []
            for name, value in request.headers.items():
                name = name.encode("ascii")
                # Prevent CVE-2015-0219
                if b"_" in name:
                    continue
                self.clean_headers.append((name.lower(), value.encode("latin1")))
            # Make sending channel
            self.reply_channel = self.main_factory.make_send_channel()
            # Tell main factory about it
            self.main_factory.reply_protocols[self.reply_channel] = self
            # Get client address if possible
            peer = self.transport.getPeer()
            host = self.transport.getHost()
            if hasattr(peer, "host") and hasattr(peer, "port"):
                self.client_addr = [six.text_type(peer.host), peer.port]
                self.server_addr = [six.text_type(host.host), host.port]
            else:
                self.client_addr = None
                self.server_addr = None

            if self.main_factory.proxy_forwarded_address_header:
                self.client_addr = parse_x_forwarded_for(
                    self.http_headers,
                    self.main_factory.proxy_forwarded_address_header,
                    self.main_factory.proxy_forwarded_port_header,
                    self.client_addr
                )

            # Make initial request info dict from request (we only have it here)
            self.path = request.path.encode("ascii")
            self.request_info = {
                "path": self.unquote(self.path),
                "headers": self.clean_headers,
                "query_string": self._raw_query_string,  # Passed by HTTP protocol
                "client": self.client_addr,
                "server": self.server_addr,
                "reply_channel": self.reply_channel,
                "order": 0,
            }
        except:
            # Exceptions here are not displayed right, just 500.
            # Turn them into an ERROR log.
            logger.error(traceback.format_exc())
            raise

        ws_protocol = None
        for header, value in self.clean_headers:
            if header == b'sec-websocket-protocol':
                protocols = [x.strip() for x in self.unquote(value).split(",")]
                for protocol in protocols:
                    if protocol in self.factory.protocols:
                        ws_protocol = protocol
                        break

        # Work out what subprotocol we will accept, if any
        if ws_protocol and ws_protocol in self.factory.protocols:
            self.protocol_to_accept = ws_protocol
        else:
            self.protocol_to_accept = None

        # Send over the connect message
        try:
            self.channel_layer.send("websocket.connect", self.request_info)
        except self.channel_layer.ChannelFull:
            # You have to consume websocket.connect according to the spec,
            # so drop the connection.
            self.muted = True
            logger.warn("WebSocket force closed for %s due to connect backpressure", self.reply_channel)
            # Send code 503 "Service Unavailable" with close.
            raise ConnectionDeny(code=503, reason="Connection queue at capacity")
        else:
            self.factory.log_action("websocket", "connecting", {
                "path": self.request.path,
                "client": "%s:%s" % tuple(self.client_addr) if self.client_addr else None,
            })

        # Make a deferred and return it - we'll either call it or err it later on
        self.handshake_deferred = defer.Deferred()
        return self.handshake_deferred

    @classmethod
    def unquote(cls, value):
        """
        Python 2 and 3 compat layer for utf-8 unquoting
        """
        if six.PY2:
            return unquote(value).decode("utf8")
        else:
            return unquote(value.decode("ascii"))

    def onOpen(self):
        # Send news that this channel is open
        logger.debug("WebSocket %s open and established", self.reply_channel)
        self.factory.log_action("websocket", "connected", {
            "path": self.request.path,
            "client": "%s:%s" % tuple(self.client_addr) if self.client_addr else None,
        })

    def onMessage(self, payload, isBinary):
        # If we're muted, do nothing.
        if self.muted:
            logger.debug("Muting incoming frame on %s", self.reply_channel)
            return
        logger.debug("WebSocket incoming frame on %s", self.reply_channel)
        self.packets_received += 1
        self.last_data = time.time()
        try:
            if isBinary:
                self.channel_layer.send("websocket.receive", {
                    "reply_channel": self.reply_channel,
                    "path": self.unquote(self.path),
                    "order": self.packets_received,
                    "bytes": payload,
                })
            else:
                self.channel_layer.send("websocket.receive", {
                    "reply_channel": self.reply_channel,
                    "path": self.unquote(self.path),
                    "order": self.packets_received,
                    "text": payload.decode("utf8"),
                })
        except self.channel_layer.ChannelFull:
            # You have to consume websocket.receive according to the spec,
            # so drop the connection.
            self.muted = True
            logger.warn("WebSocket force closed for %s due to receive backpressure", self.reply_channel)
            # Send code 1013 "try again later" with close.
            self.sendCloseFrame(code=1013, isReply=False)

    def serverAccept(self):
        """
        Called when we get a message saying to accept the connection.
        """
        self.handshake_deferred.callback(self.protocol_to_accept)
        logger.debug("WebSocket %s accepted by application", self.reply_channel)

    def serverReject(self):
        """
        Called when we get a message saying to reject the connection.
        """
        self.handshake_deferred.errback(ConnectionDeny(code=403, reason="Access denied"))
        self.cleanup()
        logger.debug("WebSocket %s rejected by application", self.reply_channel)
        self.factory.log_action("websocket", "rejected", {
            "path": self.request.path,
            "client": "%s:%s" % tuple(self.client_addr) if self.client_addr else None,
        })

    def serverSend(self, content, binary=False):
        """
        Server-side channel message to send a message.
        """
        if self.state == self.STATE_CONNECTING:
            self.serverAccept()
        self.last_data = time.time()
        logger.debug("Sent WebSocket packet to client for %s", self.reply_channel)
        if binary:
            self.sendMessage(content, binary)
        else:
            self.sendMessage(content.encode("utf8"), binary)

    def serverClose(self, code=True):
        """
        Server-side channel message to close the socket
        """
        code = 1000 if code is True else code
        self.sendClose(code=code)

    def onClose(self, wasClean, code, reason):
        self.cleanup()
        if hasattr(self, "reply_channel"):
            logger.debug("WebSocket closed for %s", self.reply_channel)
            try:
                if not self.muted:
                    self.channel_layer.send("websocket.disconnect", {
                        "reply_channel": self.reply_channel,
                        "code": code,
                        "path": self.unquote(self.path),
                        "order": self.packets_received + 1,
                    })
            except self.channel_layer.ChannelFull:
                pass
            self.factory.log_action("websocket", "disconnected", {
                "path": self.request.path,
                "client": "%s:%s" % tuple(self.client_addr) if self.client_addr else None,
            })
        else:
            logger.debug("WebSocket closed before handshake established")

    def cleanup(self):
        """
        Call to clean up this socket after it's closed.
        """
        if hasattr(self, "reply_channel"):
            if self.reply_channel in self.factory.reply_protocols:
                del self.factory.reply_protocols[self.reply_channel]

    def duration(self):
        """
        Returns the time since the socket was opened
        """
        return time.time() - self.socket_opened

    def check_ping(self):
        """
        Checks to see if we should send a keepalive ping/deny socket connection
        """
        # If we're still connecting, deny the connection
        if self.state == self.STATE_CONNECTING:
            if self.duration() > self.main_factory.websocket_connect_timeout:
                self.serverReject()
        elif self.state == self.STATE_OPEN:
            if (time.time() - self.last_data) > self.main_factory.ping_interval:
                self._sendAutoPing()
                self.last_data = time.time()


class WebSocketFactory(WebSocketServerFactory):
    """
    Factory subclass that remembers what the "main"
    factory is, so WebSocket protocols can access it
    to get reply ID info.
    """

    def __init__(self, main_factory, *args, **kwargs):
        self.main_factory = main_factory
        WebSocketServerFactory.__init__(self, *args, **kwargs)

    def log_action(self, *args, **kwargs):
        self.main_factory.log_action(*args, **kwargs)
