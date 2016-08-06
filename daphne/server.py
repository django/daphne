import logging
import socket

from twisted.internet import reactor, defer
from twisted.logger import globalLogBeginner

from .http_protocol import HTTPFactory

logger = logging.getLogger(__name__)


class Server(object):

    def __init__(
        self,
        channel_layer,
        host="127.0.0.1",
        port=8000,
        unix_socket=None,
        file_descriptor=None,
        signal_handlers=True,
        action_logger=None,
        http_timeout=120,
        websocket_timeout=None,
        ping_interval=20,
        ping_timeout=30,
        ws_protocols=None,
        root_path="",
    ):
        self.channel_layer = channel_layer
        self.host = host
        self.port = port
        self.unix_socket = unix_socket
        self.file_descriptor = file_descriptor
        self.signal_handlers = signal_handlers
        self.action_logger = action_logger
        self.http_timeout = http_timeout
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        # If they did not provide a websocket timeout, default it to the
        # channel layer's group_expiry value if present, or one day if not.
        self.websocket_timeout = websocket_timeout or getattr(channel_layer, "group_expiry", 86400)
        self.ws_protocols = ws_protocols
        self.root_path = root_path

    def run(self):
        self.factory = HTTPFactory(
            self.channel_layer,
            self.action_logger,
            timeout=self.http_timeout,
            websocket_timeout=self.websocket_timeout,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
            ws_protocols=self.ws_protocols,
            root_path=self.root_path,
        )
        # Redirect the Twisted log to nowhere
        globalLogBeginner.beginLoggingTo([lambda _: None], redirectStandardIO=False, discardBuffer=True)
        # Listen on a socket
        if self.unix_socket:
            reactor.listenUNIX(self.unix_socket, self.factory)
        elif self.file_descriptor:
            # socket returns the same socket if supplied with a fileno
            sock = socket.socket(fileno=self.file_descriptor)
            reactor.adoptStreamPort(self.file_descriptor, sock.family, self.factory)
        else:
            reactor.listenTCP(self.port, self.factory, interface=self.host)

        if "twisted" in self.channel_layer.extensions and False:
            logger.info("Using native Twisted mode on channel layer")
            reactor.callLater(0, self.backend_reader_twisted)
        else:
            logger.info("Using busy-loop synchronous mode on channel layer")
            reactor.callLater(0, self.backend_reader_sync)
        reactor.callLater(2, self.timeout_checker)
        reactor.run(installSignalHandlers=self.signal_handlers)

    def backend_reader_sync(self):
        """
        Runs as an-often-as-possible task with the reactor, unless there was
        no result previously in which case we add a small delay.
        """
        channels = self.factory.reply_channels()
        delay = 0.05
        # Quit if reactor is stopping
        if not reactor.running:
            logger.debug("Backend reader quitting due to reactor stop")
            return
        # Don't do anything if there's no channels to listen on
        if channels:
            delay = 0.01
            channel, message = self.channel_layer.receive_many(channels, block=False)
            if channel:
                delay = 0.00
                # Deal with the message
                try:
                    self.factory.dispatch_reply(channel, message)
                except Exception as e:
                    logger.error("HTTP/WS send decode error: %s" % e)
        reactor.callLater(delay, self.backend_reader_sync)

    @defer.inlineCallbacks
    def backend_reader_twisted(self):
        """
        Runs as an-often-as-possible task with the reactor, unless there was
        no result previously in which case we add a small delay.
        """
        while True:
            if not reactor.running:
                logging.debug("Backend reader quitting due to reactor stop")
                return
            channels = self.factory.reply_channels()
            if channels:
                channel, message = yield self.channel_layer.receive_many_twisted(channels)
                # Deal with the message
                if channel:
                    try:
                        self.factory.dispatch_reply(channel, message)
                    except Exception as e:
                        logger.error("HTTP/WS send decode error: %s" % e)
                else:
                    yield self.sleep(0.01)
            else:
                yield self.sleep(0.05)

    def sleep(self, delay):
        d = defer.Deferred()
        reactor.callLater(delay, d.callback, None)
        return d

    def timeout_checker(self):
        """
        Called periodically to enforce timeout rules on all connections.
        Also checks pings at the same time.
        """
        self.factory.check_timeouts()
        reactor.callLater(2, self.timeout_checker)
