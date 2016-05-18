from __future__ import unicode_literals

import logging
import six
import time
import traceback

from six.moves.urllib_parse import unquote
from twisted.protocols.policies import ProtocolWrapper
from twisted.web import http

from .ws_protocol import WebSocketProtocol, WebSocketFactory

logger = logging.getLogger(__name__)


class WebRequest(http.Request):
    """
    Request that either hands off information to channels, or offloads
    to a WebSocket class.

    Does some extra processing over the normal Twisted Web request to separate
    GET and POST out.
    """

    error_template = """
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
    """.replace("\n", "").replace("    ", " ").replace("   ", " ").replace("  ", " ")  # Shorten it a bit, bytes wise

    def __init__(self, *args, **kwargs):
        http.Request.__init__(self, *args, **kwargs)
        # Easy factory link
        self.factory = self.channel.factory
        # Make a name for our reply channel
        self.reply_channel = self.factory.channel_layer.new_channel("http.response!")
        # Tell factory we're that channel's client
        self.last_keepalive = time.time()
        self.factory.reply_protocols[self.reply_channel] = self
        self._got_response_start = False

    def process(self):
        try:
            self.request_start = time.time()
            # Get upgrade header
            upgrade_header = None
            if self.requestHeaders.hasHeader(b"Upgrade"):
                upgrade_header = self.requestHeaders.getRawHeaders(b"Upgrade")[0]
            # Get client address if possible
            if hasattr(self.client, "host") and hasattr(self.client, "port"):
                self.client_addr = [self.client.host, self.client.port]
                self.server_addr = [self.host.host, self.host.port]
            else:
                self.client_addr = None
                self.server_addr = None
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
            # Is it WebSocket? IS IT?!
            if upgrade_header and upgrade_header.lower() == b"websocket":
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
                del self.factory.reply_protocols[self.reply_channel]
                self.reply_channel = None
            # Boring old HTTP.
            else:
                # Sanitize and decode headers
                self.clean_headers = []
                for name, values in self.requestHeaders.getAllRawHeaders():
                    # Prevent CVE-2015-0219
                    if b"_" in name:
                        continue
                    for value in values:
                        self.clean_headers.append((name.lower(), value))
                logger.debug("HTTP %s request for %s", self.method, self.reply_channel)
                self.content.seek(0, 0)
                # Send message
                try:
                    self.factory.channel_layer.send("http.request", {
                        "reply_channel": self.reply_channel,
                        # TODO: Correctly say if it's 1.1 or 1.0
                        "http_version": "1.1",
                        "method": self.method.decode("ascii"),
                        "path": self.unquote(self.path),
                        "scheme": "http",
                        "query_string": self.unquote(self.query_string),
                        "headers": self.clean_headers,
                        "body": self.content.read(),
                        "client": self.client_addr,
                        "server": self.server_addr,
                    })
                except self.factory.channel_layer.ChannelFull:
                    # Channel is too full; reject request with 503
                    self.basic_error(503, b"Service Unavailable", "Request queue full.")
        except Exception as e:
            logger.error(traceback.format_exc())
            self.basic_error(500, b"Internal Server Error", "HTTP processing error")

    @classmethod
    def unquote(cls, value):
        """
        Python 2 and 3 compat layer for utf-8 unquoting
        """
        if six.PY2:
            return unquote(value).decode("utf8")
        else:
            return unquote(value.decode("ascii"))

    def send_disconnect(self):
        """
        Sends a disconnect message on the http.disconnect channel.
        Useful only really for long-polling.
        """
        try:
            self.factory.channel_layer.send("http.disconnect", {
                "reply_channel": self.reply_channel,
            })
        except self.factory.channel_layer.ChannelFull:
            pass

    def connectionLost(self, reason):
        """
        Cleans up reply channel on close.
        """
        if self.reply_channel and self.reply_channel in self.channel.factory.reply_protocols:
            self.send_disconnect()
            del self.channel.factory.reply_protocols[self.reply_channel]
        logger.debug("HTTP disconnect for %s", self.reply_channel)
        http.Request.connectionLost(self, reason)

    def finish(self):
        """
        Cleans up reply channel on close.
        """
        if self.reply_channel and self.reply_channel in self.channel.factory.reply_protocols:
            self.send_disconnect()
            del self.channel.factory.reply_protocols[self.reply_channel]
        logger.debug("HTTP close for %s", self.reply_channel)
        http.Request.finish(self)

    def serverResponse(self, message):
        """
        Writes a received HTTP response back out to the transport.
        """
        if "status" in message:
            if self._got_response_start:
                raise ValueError("Got multiple Response messages for %s!" % self.reply_channel)
            self._got_response_start = True
            # Write code
            self.setResponseCode(message['status'])
            # Write headers
            for header, value in message.get("headers", {}):
                # Shim code from old ASGI version, can be removed after a while
                if isinstance(header, six.text_type):
                    header = header.encode("latin1")
                self.responseHeaders.addRawHeader(header, value)
            logger.debug("HTTP %s response started for %s", message['status'], self.reply_channel)
        # Write out body
        if "content" in message:
            http.Request.write(self, message['content'])
        # End if there's no more content
        if not message.get("more_content", False):
            self.finish()
            logger.debug("HTTP response complete for %s", self.reply_channel)
            try:
                self.factory.log_action("http", "complete", {
                    "path": self.path.decode("ascii"),
                    "status": self.code,
                    "method": self.method.decode("ascii"),
                    "client": "%s:%s" % tuple(self.client_addr) if self.client_addr else None,
                    "time_taken": self.duration(),
                    "size": self.sentLength,
                })
            except Exception as e:
                logging.error(traceback.format_exc())
        else:
            logger.debug("HTTP response chunk for %s", self.reply_channel)

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
        self.serverResponse({
            "status": status,
            "status_text": status_text,
            "headers": [
                (b"Content-Type", b"text/html; charset=utf-8"),
            ],
            "content": (self.error_template % {
                "title": str(status) + " " + status_text.decode("ascii"),
                "body": body,
            }).encode("utf8"),
        })



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

    def __init__(self, channel_layer, action_logger=None, timeout=120, websocket_timeout=86400, ping_interval=20, ws_protocols=None):
        http.HTTPFactory.__init__(self)
        self.channel_layer = channel_layer
        self.action_logger = action_logger
        self.timeout = timeout
        self.websocket_timeout = websocket_timeout
        self.ping_interval = ping_interval
        # We track all sub-protocols for response channel mapping
        self.reply_protocols = {}
        # Make a factory for WebSocket protocols
        self.ws_factory = WebSocketFactory(self, protocols=ws_protocols)
        self.ws_factory.protocol = WebSocketProtocol
        self.ws_factory.reply_protocols = self.reply_protocols

    def reply_channels(self):
        return self.reply_protocols.keys()

    def dispatch_reply(self, channel, message):
        if channel.startswith("http") and isinstance(self.reply_protocols[channel], WebRequest):
            self.reply_protocols[channel].serverResponse(message)
        elif channel.startswith("websocket") and isinstance(self.reply_protocols[channel], WebSocketProtocol):
            # Ensure the message is a valid WebSocket one
            unknown_message_keys = set(message.keys()) - {"bytes", "text", "close"}
            if unknown_message_keys:
                raise ValueError(
                    "Got invalid WebSocket reply message on %s - contains unknown keys %s" % (
                        channel,
                        unknown_message_keys,
                    )
                )
            if message.get("bytes", None):
                self.reply_protocols[channel].serverSend(message["bytes"], True)
            if message.get("text", None):
                self.reply_protocols[channel].serverSend(message["text"], False)
            if message.get("close", False):
                self.reply_protocols[channel].serverClose()
        else:
            raise ValueError("Cannot dispatch message on channel %r" % channel)

    def log_action(self, protocol, action, details):
        """
        Dispatches to any registered action logger, if there is one.
        """
        if self.action_logger:
            self.action_logger(protocol, action, details)

    def check_timeouts(self):
        """
        Runs through all HTTP protocol instances and times them out if they've
        taken too long (and so their message is probably expired)
        """
        for protocol in list(self.reply_protocols.values()):
            # Web timeout checking
            if isinstance(protocol, WebRequest) and protocol.duration() > self.timeout:
                protocol.basic_error(503, b"Service Unavailable", "Worker server failed to respond within time limit.")
            # WebSocket timeout checking and keepalive ping sending
            elif isinstance(protocol, WebSocketProtocol):
                # Timeout check
                if protocol.duration() > self.websocket_timeout:
                    protocol.serverClose()
                # Ping check
                else:
                    protocol.check_ping()
