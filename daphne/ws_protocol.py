from __future__ import unicode_literals

import logging
import time
import traceback
from six.moves.urllib.parse import urlencode

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
        try:
            # Sanitize and decode headers
            clean_headers = {}
            for name, value in request.headers.items():
                # Prevent CVE-2015-0219
                if "_" in name:
                    continue
                clean_headers[name.lower()] = value.encode("latin1")
            # Reconstruct query string
            # TODO: get autobahn to provide it raw
            query_string = urlencode(request.params).encode("ascii")
            # Make sending channel
            self.reply_channel = self.channel_layer.new_channel("!websocket.send.?")
            # Tell main factory about it
            self.main_factory.reply_protocols[self.reply_channel] = self
            # Make initial request info dict from request (we only have it here)
            self.request_info = {
                "path": request.path.encode("ascii"),
                "headers": clean_headers,
                "query_string": query_string,
                "client": [self.transport.getPeer().host, self.transport.getPeer().port],
                "server": [self.transport.getHost().host, self.transport.getHost().port],
                "reply_channel": self.reply_channel,
                "order": 0,
            }
        except:
            # Exceptions here are not displayed right, just 500.
            # Turn them into an ERROR log.
            logger.error(traceback.format_exc())
            raise

    def onOpen(self):
        # Send news that this channel is open
        logger.debug("WebSocket open for %s", self.reply_channel)
        self.channel_layer.send("websocket.connect", self.request_info)
        self.factory.log_action("websocket", "connected", {
            "path": self.request.path,
            "client": "%s:%s" % (self.transport.getPeer().host, self.transport.getPeer().port),
        })

    def onMessage(self, payload, isBinary):
        logger.debug("WebSocket incoming packet on %s", self.reply_channel)
        self.packets_received += 1
        if isBinary:
            self.channel_layer.send("websocket.receive", {
                "reply_channel": self.reply_channel,
                "order": self.packets_received,
                "bytes": payload,
            })
        else:
            self.channel_layer.send("websocket.receive", {
                "reply_channel": self.reply_channel,
                "order": self.packets_received,
                "text": payload.decode("utf8"),
            })

    def serverSend(self, content, binary=False):
        """
        Server-side channel message to send a message.
        """
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
            self.channel_layer.send("websocket.disconnect", {
                "reply_channel": self.reply_channel,
                "order": self.packets_received + 1,
            })
            self.factory.log_action("websocket", "disconnected", {
                "path": self.request.path,
                "client": "%s:%s" % (self.transport.getPeer().host, self.transport.getPeer().port),
            })
        else:
            logger.debug("WebSocket closed before handshake established")


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
