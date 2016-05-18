from __future__ import unicode_literals

import logging
import six
import time
import traceback
from six.moves.urllib_parse import unquote, urlencode

from autobahn.twisted.websocket import WebSocketServerProtocol, WebSocketServerFactory

logger = logging.getLogger(__name__)


class WebSocketProtocol(WebSocketServerProtocol):
    """
    Protocol which supports WebSockets and forwards incoming messages to
    the websocket channels.
    """

    def set_main_factory(self, main_factory):
        self.main_factory = main_factory
        self.channel_layer = self.main_factory.channel_layer

    def onConnect(self, request):
        self.request = request
        self.packets_received = 0
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
            # Reconstruct query string
            # TODO: get autobahn to provide it raw
            query_string = urlencode(request.params, doseq=True).encode("ascii")
            # Make sending channel
            self.reply_channel = self.channel_layer.new_channel("websocket.send!")
            # Tell main factory about it
            self.main_factory.reply_protocols[self.reply_channel] = self
            # Get client address if possible
            if hasattr(self.transport.getPeer(), "host") and hasattr(self.transport.getPeer(), "port"):
                self.client_addr = [self.transport.getPeer().host, self.transport.getPeer().port]
                self.server_addr = [self.transport.getHost().host, self.transport.getHost().port]
            else:
                self.client_addr = None
                self.server_addr = None
            # Make initial request info dict from request (we only have it here)
            self.path = request.path.encode("ascii")
            self.request_info = {
                "path": self.unquote(self.path),
                "headers": self.clean_headers,
                "query_string": self.unquote(query_string),
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
            if header == 'sec-websocket-protocol':
                ws_protocol = value
        if ws_protocol and ws_protocol in self.factory.protocols:
            return ws_protocol

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
        logger.debug("WebSocket open for %s", self.reply_channel)
        try:
            self.channel_layer.send("websocket.connect", self.request_info)
        except self.channel_layer.ChannelFull:
            # We don't drop the connection here as you don't _have_ to consume websocket.connect
            pass
        self.factory.log_action("websocket", "connected", {
            "path": self.request.path,
            "client": "%s:%s" % tuple(self.client_addr) if self.client_addr else None,
        })

    def onMessage(self, payload, isBinary):
        logger.debug("WebSocket incoming packet on %s", self.reply_channel)
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
            # We don't drop the connection here as you don't _have_ to consume websocket.receive
            # TODO: Maybe add an option to drop if this is backlogged?
            pass

    def serverSend(self, content, binary=False):
        """
        Server-side channel message to send a message.
        """
        self.last_data = time.time()
        logger.debug("Sent WebSocket packet to client for %s", self.reply_channel)
        if binary:
            self.sendMessage(content, binary)
        else:
            self.sendMessage(content.encode("utf8"), binary)

    def serverClose(self):
        """
        Server-side channel message to close the socket
        """
        self.sendClose()

    def onClose(self, wasClean, code, reason):
        if hasattr(self, "reply_channel"):
            logger.debug("WebSocket closed for %s", self.reply_channel)
            del self.factory.reply_protocols[self.reply_channel]
            try:
                self.channel_layer.send("websocket.disconnect", {
                    "reply_channel": self.reply_channel,
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

    def duration(self):
        """
        Returns the time since the socket was opened
        """
        return time.time() - self.socket_opened

    def check_ping(self):
        """
        Checks to see if we should send a keepalive ping.
        """
        if (time.time() - self.last_data) > self.main_factory.ping_interval:
            self.sendPing()
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
