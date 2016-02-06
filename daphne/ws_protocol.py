from __future__ import unicode_literals

import time
import logging

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
        self.request_info = {
            "path": request.path,
            "headers": self.headers,
            "query_string": request.query_string,
            "client": [request.client.host, request.client.port],
            "server": [request.host.host, request.host.port],
        }

    def onOpen(self):
        # Make sending channel
        self.reply_channel = self.channel_layer.new_channel("!websocket.send.?")
        self.request_info["reply_channel"] = self.reply_channel
        self.last_keepalive = time.time()
        # Tell main factory about it
        self.main_factory.reply_protocols[self.reply_channel] = self
        # Send news that this channel is open
        logger.debug("WebSocket open for %s", self.reply_channel)
        self.channel_layer.send("websocket.connect", self.request_info)

    def onMessage(self, payload, isBinary):
        logger.debug("WebSocket incoming packet on %s", self.reply_channel)
        if isBinary:
            self.channel_layer.send("websocket.receive", {
                "reply_channel": self.reply_channel,
                "bytes": payload,
            })
        else:
            self.channel_layer.send("websocket.receive", {
                "reply_channel": self.reply_channel,
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
        logger.debug("WebSocket closed for %s", self.reply_channel)
        if hasattr(self, "reply_channel"):
            del self.factory.reply_protocols[self.reply_channel]
            self.channel_layer.send("websocket.disconnect", {
                "reply_channel": self.reply_channel,
            })

    def sendKeepalive(self):
        """
        Sends a keepalive packet on the keepalive channel.
        """
        self.channel_layer.send("websocket.keepalive", {
            "reply_channel": self.reply_channel,
        })
        self.last_keepalive = time.time()


class WebSocketFactory(WebSocketServerFactory):
    """
    Factory subclass that remembers what the "main"
    factory is, so WebSocket protocols can access it
    to get reply ID info.
    """

    def __init__(self, main_factory, *args, **kwargs):
        self.main_factory = main_factory
        WebSocketServerFactory.__init__(self, *args, **kwargs)
