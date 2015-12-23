import time

from autobahn.twisted.websocket import WebSocketServerProtocol, WebSocketServerFactory

from django.http import parse_cookie


class WebSocketProtocol(WebSocketServerProtocol):
    """
    Protocol which supports WebSockets and forwards incoming messages to
    the websocket channels.
    """

    def __init__(self, *args, **kwargs):
        WebSocketServerProtocol.__init__(self, *args, **kwargs)
        # Easy parent factory/channel layer link
        self.main_factory = self.factory.main_factory
        self.channel_layer = self.main_factory.channel_layer

    def onConnect(self, request):
        self.request_info = {
            "path": request.path,
            "get": request.params,
            "cookies": parse_cookie(request.headers.get('cookie', ''))
        }

    def onOpen(self):
        # Make sending channel
        self.reply_channel = self.channel_layer.new_channel("!websocket.send.?")
        self.request_info["reply_channel"] = self.reply_channel
        self.last_keepalive = time.time()
        # Tell main factory about it
        self.main_factory.reply_protocols[self.reply_channel] = self
        # Send news that this channel is open
        self.channel_layer.send("websocket.connect", self.request_info)

    def onMessage(self, payload, isBinary):
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
