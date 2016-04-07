import logging
from twisted.internet import reactor

from .http_protocol import HTTPFactory

logger = logging.getLogger(__name__)


class Server(object):

    def __init__(
        self,
        channel_layer,
        host="127.0.0.1",
        port=8000,
        unix_socket=None,
        signal_handlers=True,
        action_logger=None,
        http_timeout=120,
        websocket_timeout=None,
        ping_interval=20,
        ws_protocols=None,
    ):
        self.channel_layer = channel_layer
        self.host = host
        self.port = port
        self.unix_socket = unix_socket
        self.signal_handlers = signal_handlers
        self.action_logger = action_logger
        self.http_timeout = http_timeout
        self.ping_interval = ping_interval
        # If they did not provide a websocket timeout, default it to the
        # channel layer's group_expiry value if present, or one day if not.
        self.websocket_timeout = websocket_timeout or getattr(channel_layer, "group_expiry", 86400)
        self.ws_protocols = ws_protocols

    def run(self):
        self.factory = HTTPFactory(
            self.channel_layer,
            self.action_logger,
            timeout=self.http_timeout,
            websocket_timeout=self.websocket_timeout,
            ping_interval=self.ping_interval,
            ws_protocols=self.ws_protocols,
        )
        if self.unix_socket:
            reactor.listenUNIX(self.unix_socket, self.factory)
        else:
            reactor.listenTCP(self.port, self.factory, interface=self.host)
        reactor.callLater(0, self.backend_reader)
        reactor.callLater(2, self.timeout_checker)
        reactor.run(installSignalHandlers=self.signal_handlers)

    def backend_reader(self):
        """
        Runs as an-often-as-possible task with the reactor, unless there was
        no result previously in which case we add a small delay.
        """
        channels = self.factory.reply_channels()
        delay = 0.05
        # Quit if reactor is stopping
        if not reactor.running:
            logging.debug("Backend reader quitting due to reactor stop")
            return
        # Don't do anything if there's no channels to listen on
        if channels:
            delay = 0.01
            channel, message = self.channel_layer.receive_many(channels, block=False)
            if channel:
                delay = 0
                # Deal with the message
                self.factory.dispatch_reply(channel, message)
        reactor.callLater(delay, self.backend_reader)

    def timeout_checker(self):
        """
        Called periodically to enforce timeout rules on all connections.
        Also checks pings at the same time.
        """
        self.factory.check_timeouts()
        reactor.callLater(2, self.timeout_checker)
