import time
from twisted.internet import reactor

from .http_protocol import HTTPFactory


class Server(object):

    def __init__(self, channel_layer, host="127.0.0.1", port=8000, signal_handlers=True):
        self.channel_layer = channel_layer
        self.host = host
        self.port = port
        self.signal_handlers = signal_handlers

    def run(self):
        self.factory = HTTPFactory(self.channel_layer)
        reactor.listenTCP(self.port, self.factory, interface=self.host)
        reactor.callInThread(self.backend_reader)
        reactor.run(installSignalHandlers=self.signal_handlers)

    def backend_reader(self):
        """
        Run in a separate thread; reads messages from the backend.
        """
        while True:
            channels = self.factory.reply_channels()
            # Quit if reactor is stopping
            if not reactor.running:
                return
            # Don't do anything if there's no channels to listen on
            if channels:
                channel, message = self.channel_layer.receive_many(channels, block=True)
            else:
                time.sleep(0.1)
                continue
            # Wait around if there's nothing received
            if channel is None:
                time.sleep(0.05)
                continue
            # Deal with the message
            self.factory.dispatch_reply(channel, message)
