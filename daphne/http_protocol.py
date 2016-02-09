from __future__ import unicode_literals

import logging
import six
import time

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
        self.reply_channel = self.factory.channel_layer.new_channel("!http.response.?")
        # Tell factory we're that channel's client
        self.last_keepalive = time.time()
        self.factory.reply_protocols[self.reply_channel] = self
        self._got_response_start = False

    def process(self):
        # Get upgrade header
        upgrade_header = None
        if self.requestHeaders.hasHeader("Upgrade"):
            upgrade_header = self.requestHeaders.getRawHeaders("Upgrade")[0]
        # Calculate query string
        self.query_string = ""
        if b"?" in self.uri:
            self.query_string = self.uri.split(b"?", 1)[1]
        # Is it WebSocket? IS IT?!
        if upgrade_header == "websocket":
            # Make WebSocket protocol to hand off to
            protocol = self.factory.ws_factory.buildProtocol(self.transport.getPeer())
            if not protocol:
                # If protocol creation fails, we signal "internal server error"
                self.setResponseCode(500)
                logger.warn("Could not make WebSocket protocol")
                self.finish()
            # Port across transport
            protocol.set_main_factory(self.factory)
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
            logger.debug("Upgraded connection %s to WebSocket %s", self.reply_channel, protocol.reply_channel)
            self.factory.reply_protocols[self.reply_channel] = None
            self.reply_channel = None
        # Boring old HTTP.
        else:
            # Sanitize and decode headers
            self.clean_headers = {}
            for name, value in self.requestHeaders.getAllRawHeaders():
                # Prevent CVE-2015-0219
                if b"_" in name:
                    continue
                self.clean_headers[name.lower().decode("latin1")] = value[0]
            logger.debug("HTTP %s request for %s", self.method, self.reply_channel)
            self.content.seek(0, 0)
            # Send message
            self.factory.channel_layer.send("http.request", {
                "reply_channel": self.reply_channel,
                # TODO: Correctly say if it's 1.1 or 1.0
                "http_version": "1.1",
                "method": self.method.decode("ascii"),
                "path": self.path,
                "scheme": "http",
                "query_string": self.query_string,
                "headers": self.clean_headers,
                "body": self.content.read(),
                "client": [self.client.host, self.client.port],
                "server": [self.host.host, self.host.port],
            })

    def connectionLost(self, reason):
        """
        Cleans up reply channel on close.
        """
        if self.reply_channel:
            del self.channel.factory.reply_protocols[self.reply_channel]
        logger.debug("HTTP disconnect for %s", self.reply_channel)
        http.Request.connectionLost(self, reason)

    def serverResponse(self, message):
        """
        Writes a received HTTP response back out to the transport.
        """
        if "status" in message:
            if self._got_response_start:
                raise ValueError("Got multiple Response messages!")
            self._got_response_start = True
            # Write code
            status_text = message.get("status_text", None)
            if isinstance(status_text, six.text_type):
                logger.warn("HTTP status text for %s was text - should be bytes", self.reply_channel)
                status_text = status_text.encode("ascii")
            self.setResponseCode(message['status'], )
            # Write headers
            for header, value in message.get("headers", {}):
                self.setHeader(header.encode("utf8"), value)
        # Write out body
        if "content" in message:
            http.Request.write(self, message['content'])
        # End if there's no more content
        if not message.get("more_content", False):
            self.finish()
            logger.debug("HTTP %s response for %s", message['status'], self.reply_channel)
        else:
            logger.debug("HTTP %s response chunk for %s", message['status'], self.reply_channel)


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
