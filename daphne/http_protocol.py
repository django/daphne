from __future__ import unicode_literals

import time
import logging
from twisted.python.compat import _PY3
from twisted.web import http
from twisted.protocols.policies import ProtocolWrapper

from .ws_protocol import WebSocketProtocol, WebSocketFactory

logger = logging.getLogger(__name__)


class WebRequest(http.Request):
    """
    Request that either hands off information to channels, or offloads
    to a WebSocket class.

    Does some extra processing over the normal Twisted Web request to separate
    GET and POST out.
    """

    def __init__(self, *args, **kwargs):
        http.Request.__init__(self, *args, **kwargs)
        # Easy factory link
        self.factory = self.channel.factory
        # Make a name for our reply channel
        self.reply_channel = self.factory.channel_layer.new_channel(b"!http.response.?")
        # Tell factory we're that channel's client
        self.last_keepalive = time.time()
        self.factory.reply_protocols[self.reply_channel] = self

    def process(self):
        # Get upgrade header
        upgrade_header = None
        if self.requestHeaders.hasHeader("Upgrade"):
            upgrade_header = self.requestHeaders.getRawHeaders("Upgrade")[0]
        # Is it WebSocket? IS IT?!
        if upgrade_header == "websocket":
            # Make WebSocket protocol to hand off to
            protocol = self.factory.ws_factory.buildProtocol(self.transport.getPeer())
            if not protocol:
                # If protocol creation fails, we signal "internal server error"
                self.setResponseCode(500)
                self.finish()
            # Port across transport
            transport, self.transport = self.transport, None
            if isinstance(transport, ProtocolWrapper):
                # i.e. TLS is a wrapping protocol
                transport.wrappedProtocol = protocol
            else:
                transport.protocol = protocol
            protocol.makeConnection(transport)
            # Re-inject request
            data = self.method + b' ' + self.uri + b' HTTP/1.1\x0d\x0a'
            for h in self.requestHeaders.getAllRawHeaders():
                data += h[0] + b': ' + b",".join(h[1]) + b'\x0d\x0a'
            data += b"\x0d\x0a"
            data += self.content.read()
            protocol.dataReceived(data)
            # Remove our HTTP reply channel association
            logging.debug("Upgraded connection %s to WebSocket", self.reply_channel)
            self.factory.reply_protocols[self.reply_channel] = None
            self.reply_channel = None
        # Boring old HTTP.
        else:
            # Send request message
            logging.debug("HTTP %s request for %s", self.method, self.reply_channel)
            self.factory.channel_layer.send(b"http.request", {
                "reply_channel": self.reply_channel,
                "method": self.method,
                "get": self.get,
                "post": self.post,
                "cookies": self.received_cookies,
                "headers": {k: v[0] for k, v in self.requestHeaders.getAllRawHeaders()},
                "client": [self.client.host, self.client.port],
                "server": [self.host.host, self.host.port],
                "path": self.path,
            })

    def connectionLost(self, reason):
        """
        Cleans up reply channel on close.
        """
        if self.reply_channel:
            del self.channel.factory.reply_protocols[self.reply_channel]
        logging.debug("HTTP disconnect for %s", self.reply_channel)
        http.Request.connectionLost(self, reason)

    def serverResponse(self, message):
        """
        Writes a received HTTP response back out to the transport.
        """
        # Write code
        self.setResponseCode(message['status'])
        # Write headers
        for header, value in message.get("headers", {}):
            self.setHeader(header.encode("utf8"), value.encode("utf8"))
        # Write cookies
        for cookie in message.get("cookies"):
            self.cookies.append(cookie.encode("utf8"))
        # Write out body
        if "content" in message:
            http.Request.write(self, message['content'].encode("utf8"))
        self.finish()
        logging.debug("HTTP %s response for %s", message['status'], self.reply_channel)

    def requestReceived(self, command, path, version):
        """
        Called by channel when all data has been received.
        Overridden because Twisted merges GET and POST into one thing by default.
        """
        self.content.seek(0,0)
        self.get = {}
        self.post = {}

        self.method, self.uri = command, path
        self.clientproto = version
        x = self.uri.split(b'?', 1)

        # URI and GET args assignment
        if len(x) == 1:
            self.path = self.uri
        else:
            self.path, argstring = x
            self.get = http.parse_qs(argstring, 1)

        # cache the client and server information, we'll need this later to be
        # serialized and sent with the request so CGIs will work remotely
        self.client = self.channel.transport.getPeer()
        self.host = self.channel.transport.getHost()

        # Argument processing
        ctype = self.requestHeaders.getRawHeaders(b'content-type')
        if ctype is not None:
            ctype = ctype[0]

        # Process POST data if present
        if self.method == b"POST" and ctype:
            mfd = b'multipart/form-data'
            key, pdict = http._parseHeader(ctype)
            if key == b'application/x-www-form-urlencoded':
                self.post.update(http.parse_qs(self.content.read(), 1))
            elif key == mfd:
                try:
                    cgiArgs = cgi.parse_multipart(self.content, pdict)

                    if _PY3:
                        # parse_multipart on Python 3 decodes the header bytes
                        # as iso-8859-1 and returns a str key -- we want bytes
                        # so encode it back
                        self.post.update({x.encode('iso-8859-1'): y
                                          for x, y in cgiArgs.items()})
                    else:
                        self.post.update(cgiArgs)
                except:
                    # It was a bad request.
                    http._respondToBadRequestAndDisconnect(self.channel.transport)
                    return
            self.content.seek(0, 0)

        # Continue with rest of request handling
        self.process()


class HTTPProtocol(http.HTTPChannel):

    requestFactory = WebRequest


class HTTPFactory(http.HTTPFactory):
    """
    Factory which takes care of tracking which protocol
    instances or request instances are responsible for which
    named response channels, so incoming messages can be
    routed appropriately.
    """

    protocol = HTTPProtocol

    def __init__(self, channel_layer):
        http.HTTPFactory.__init__(self)
        self.channel_layer = channel_layer
        # We track all sub-protocols for response channel mapping
        self.reply_protocols = {}
        # Make a factory for WebSocket protocols
        self.ws_factory = WebSocketFactory(self)
        self.ws_factory.protocol = WebSocketProtocol
        self.ws_factory.reply_protocols = self.reply_protocols

    def reply_channels(self):
        return self.reply_protocols.keys()

    def dispatch_reply(self, channel, message):
        if channel.startswith("!http") and isinstance(self.reply_protocols[channel], WebRequest):
            self.reply_protocols[channel].serverResponse(message)
        elif channel.startswith("!websocket") and isinstance(self.reply_protocols[channel], WebSocketProtocol):
            if message.get("bytes", None):
                self.reply_protocols[channel].serverSend(message["bytes"], True)
            if message.get("text", None):
                self.reply_protocols[channel].serverSend(message["text"], False)
            if message.get("close", False):
                self.reply_protocols[channel].serverClose()
        else:
            raise ValueError("Cannot dispatch message on channel %r" % channel)
