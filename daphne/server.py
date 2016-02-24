import logging
from twisted.internet import reactor

from .http_protocol import HTTPFactory

logger = logging.getLogger(__name__)


class Server(object):

    def __init__(self, channel_layer, host="127.0.0.1", port=8000, signal_handlers=True, action_logger=None):
        self.channel_layer = channel_layer
        self.host = host
        self.port = port
        self.signal_handlers = signal_handlers
        self.action_logger = action_logger

    def run(self):
        self.factory = HTTPFactory(self.channel_layer, self.action_logger)
        reactor.listenTCP(self.port, self.factory, interface=self.host)
        reactor.callLater(0, self.backend_reader)
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
