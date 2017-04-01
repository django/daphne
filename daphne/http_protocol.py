from __future__ import unicode_literals

import logging
import random
import six
import string
import time
import traceback

from zope.interface import implementer

from six.moves.urllib_parse import unquote, unquote_plus
from twisted.internet.interfaces import IProtocolNegotiationFactory
from twisted.protocols.policies import ProtocolWrapper
from twisted.web import http

from .utils import parse_x_forwarded_for
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
        try:
            http.Request.__init__(self, *args, **kwargs)
            # Easy factory link
            self.factory = self.channel.factory
            # Make a name for our reply channel
            self.reply_channel = self.factory.make_send_channel()
            # Tell factory we're that channel's client
            self.last_keepalive = time.time()
            self.factory.reply_protocols[self.reply_channel] = self
            self._got_response_start = False
        except Exception:
            logger.error(traceback.format_exc())
            raise

    def process(self):
        try:
            self.request_start = time.time()
            # Get upgrade header
            upgrade_header = None
            if self.requestHeaders.hasHeader(b"Upgrade"):
                upgrade_header = self.requestHeaders.getRawHeaders(b"Upgrade")[0]
            # Get client address if possible
            if hasattr(self.client, "host") and hasattr(self.client, "port"):
                # client.host and host.host are byte strings in Python 2, but spec
                # requires unicode string.
                self.client_addr = [six.text_type(self.client.host), self.client.port]
                self.server_addr = [six.text_type(self.host.host), self.host.port]
            else:
                self.client_addr = None
                self.server_addr = None

            if self.factory.proxy_forwarded_address_header:
                self.client_addr = parse_x_forwarded_for(
                    self.requestHeaders,
                    self.factory.proxy_forwarded_address_header,
                    self.factory.proxy_forwarded_port_header,
                    self.client_addr
                )

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
                if hasattr(protocol, "reply_channel"):
                    logger.debug("Upgraded connection %s to WebSocket %s", self.reply_channel, protocol.reply_channel)
                else:
                    logger.debug("Connection %s did not get successful WS handshake.", self.reply_channel)
                del self.factory.reply_protocols[self.reply_channel]
                self.reply_channel = None
                # Resume the producer so we keep getting data, if it's available as a method
                # 17.1 version
                if hasattr(self.channel, "_networkProducer"):
                    self.channel._networkProducer.resumeProducing()
                # 16.x version
                elif hasattr(self.channel, "resumeProducing"):
                    self.channel.resumeProducing()

            # Boring old HTTP.
            else:
                # Sanitize and decode headers, potentially extracting root path
                self.clean_headers = []
                self.root_path = self.factory.root_path
                for name, values in self.requestHeaders.getAllRawHeaders():
                    # Prevent CVE-2015-0219
                    if b"_" in name:
                        continue
                    for value in values:
                        if name.lower() == b"daphne-root-path":
                            self.root_path = self.unquote(value)
                        else:
                            self.clean_headers.append((name.lower(), value))
                logger.debug("HTTP %s request for %s", self.method, self.reply_channel)
                self.content.seek(0, 0)
                # Send message
                try:
                    self.factory.channel_layer.send("http.request", {
                        "reply_channel": self.reply_channel,
                        # TODO: Correctly say if it's 1.1 or 1.0
                        "http_version": self.clientproto.split(b"/")[-1].decode("ascii"),
                        "method": self.method.decode("ascii"),
                        "path": self.unquote(self.path),
                        "root_path": self.root_path,
                        "scheme": "https" if self.isSecure() else "http",
                        "query_string": self.query_string,
                        "headers": self.clean_headers,
                        "body": self.content.read(),
                        "client": self.client_addr,
                        "server": self.server_addr,
                    })
                except self.factory.channel_layer.ChannelFull:
                    # Channel is too full; reject request with 503
                    self.basic_error(503, b"Service Unavailable", "Request queue full.")
        except Exception:
            logger.error(traceback.format_exc())
            self.basic_error(500, b"Internal Server Error", "HTTP processing error")

    @classmethod
    def unquote(cls, value, plus_as_space=False):
        """
        Python 2 and 3 compat layer for utf-8 unquoting
        """
        if six.PY2:
            if plus_as_space:
                return unquote_plus(value).decode("utf8")
            else:
                return unquote(value).decode("utf8")
        else:
            if plus_as_space:
                return unquote_plus(value.decode("ascii"))
            else:
                return unquote(value.decode("ascii"))

    def send_disconnect(self):
        """
        Sends a disconnect message on the http.disconnect channel.
        Useful only really for long-polling.
        """
        # If we don't yet have a path, then don't send as we never opened.
        if self.path:
            try:
                self.factory.channel_layer.send("http.disconnect", {
                    "reply_channel": self.reply_channel,
                    "path": self.unquote(self.path),
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
        if not self._got_response_start:
            self._got_response_start = True
            if 'status' not in message:
                raise ValueError("Specifying a status code is required for a Response message.")

            # Set HTTP status code
            self.setResponseCode(message['status'])
            # Write headers
            for header, value in message.get("headers", {}):
                # Shim code from old ASGI version, can be removed after a while
                if isinstance(header, six.text_type):
                    header = header.encode("latin1")
                self.responseHeaders.addRawHeader(header, value)
            logger.debug("HTTP %s response started for %s", message['status'], self.reply_channel)
        else:
            if 'status' in message:
                raise ValueError("Got multiple Response messages for %s!" % self.reply_channel)

        # Write out body
        http.Request.write(self, message.get('content', b''))

        # End if there's no more content
        if not message.get("more_content", False):
            self.finish()
            logger.debug("HTTP response complete for %s", self.reply_channel)
            try:
                self.factory.log_action("http", "complete", {
                    "path": self.uri.decode("ascii"),
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
                "title": six.text_type(status) + " " + status_text.decode("ascii"),
                "body": body,
            }).encode("utf8"),
        })


@implementer(IProtocolNegotiationFactory)
class HTTPFactory(http.HTTPFactory):
    """
    Factory which takes care of tracking which protocol
    instances or request instances are responsible for which
    named response channels, so incoming messages can be
    routed appropriately.
    """

    def __init__(self, channel_layer, action_logger=None, send_channel=None, timeout=120, websocket_timeout=86400, ping_interval=20, ping_timeout=30, ws_protocols=None, root_path="", websocket_connect_timeout=30, proxy_forwarded_address_header=None, proxy_forwarded_port_header=None):
        http.HTTPFactory.__init__(self)
        self.channel_layer = channel_layer
        self.action_logger = action_logger
        self.send_channel = send_channel
        assert self.send_channel is not None
        self.timeout = timeout
        self.websocket_timeout = websocket_timeout
        self.websocket_connect_timeout = websocket_connect_timeout
        self.ping_interval = ping_interval
        self.proxy_forwarded_address_header = proxy_forwarded_address_header
        self.proxy_forwarded_port_header = proxy_forwarded_port_header
        # We track all sub-protocols for response channel mapping
        self.reply_protocols = {}
        # Make a factory for WebSocket protocols
        self.ws_factory = WebSocketFactory(self, protocols=ws_protocols)
        self.ws_factory.setProtocolOptions(
            autoPingTimeout=ping_timeout,
            allowNullOrigin=True,
        )
        self.ws_factory.protocol = WebSocketProtocol
        self.ws_factory.reply_protocols = self.reply_protocols
        self.root_path = root_path

    def buildProtocol(self, addr):
        """
        Builds protocol instances. This override is used to ensure we use our
        own Request object instead of the default.
        """
        try:
            protocol = http.HTTPFactory.buildProtocol(self, addr)
            protocol.requestFactory = WebRequest
            return protocol
        except Exception as e:
            logger.error("Cannot build protocol: %s" % traceback.format_exc())
            raise

    def make_send_channel(self):
        """
        Makes a new send channel for a protocol with our process prefix.
        """
        protocol_id = "".join(random.choice(string.ascii_letters) for i in range(10))
        return self.send_channel + protocol_id

    def reply_channels(self):
        return self.reply_protocols.keys()

    def dispatch_reply(self, channel, message):
        if channel not in self.reply_protocols:
            raise ValueError("Cannot dispatch message on channel %r (unknown)" % channel)

        if isinstance(self.reply_protocols[channel], WebRequest):
            self.reply_protocols[channel].serverResponse(message)
        elif isinstance(self.reply_protocols[channel], WebSocketProtocol):
            # Switch depending on current socket state
            protocol = self.reply_protocols[channel]
            # See if the message is valid
            unknown_keys = set(message.keys()) - {"bytes", "text", "close", "accept"}
            if unknown_keys:
                raise ValueError(
                    "Got invalid WebSocket reply message on %s - "
                    "contains unknown keys %s (looking for either {'accept', 'text', 'bytes', 'close'})" % (
                        channel,
                        unknown_keys,
                    )
                )
            # Accepts allow bytes/text afterwards
            if message.get("accept", None) and protocol.state == protocol.STATE_CONNECTING:
                protocol.serverAccept()
            # Rejections must be the only thing
            if message.get("accept", None) == False and protocol.state == protocol.STATE_CONNECTING:
                protocol.serverReject()
                return
            # You're only allowed one of bytes or text
            if message.get("bytes", None) and message.get("text", None):
                raise ValueError(
                    "Got invalid WebSocket reply message on %s - contains both bytes and text keys" % (
                        channel,
                    )
                )
            if message.get("bytes", None):
                protocol.serverSend(message["bytes"], True)
            if message.get("text", None):
                protocol.serverSend(message["text"], False)

            closing_code = message.get("close", False)
            if closing_code:
                if protocol.state == protocol.STATE_CONNECTING:
                    protocol.serverReject()
                else:
                    protocol.serverClose(code=closing_code)
        else:
            raise ValueError("Unknown protocol class")

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
                if protocol.duration() > self.websocket_timeout and self.websocket_timeout >= 0:
                    protocol.serverClose()
                # Ping check
                else:
                    protocol.check_ping()

    # IProtocolNegotiationFactory
    def acceptableProtocols(self):
        """
        Protocols this server can speak after ALPN negotiation. Currently that
        is HTTP/1.1 and optionally HTTP/2. Websockets cannot be negotiated
        using ALPN, so that doesn't go here: anyone wanting websockets will
        negotiate HTTP/1.1 and then do the upgrade dance.
        """
        baseProtocols = [b'http/1.1']

        if http.H2_ENABLED:
            baseProtocols.insert(0, b'h2')

        return baseProtocols
