from __future__ import unicode_literals

import collections
import logging
import random
import string
import warnings

from twisted.internet import reactor, defer
from twisted.internet.endpoints import serverFromString
from twisted.logger import globalLogBeginner, STDLibLogObserver
from twisted.web import http
from twisted.python.threadpool import ThreadPool

from .http_protocol import HTTPFactory
from .ws_protocol import WebSocketFactory

logger = logging.getLogger(__name__)


class Server(object):

    def __init__(
        self,
        channel_layer,
        consumer,
        endpoints=None,
        signal_handlers=True,
        action_logger=None,
        http_timeout=120,
        websocket_timeout=None,
        websocket_connect_timeout=20,
        ping_interval=20,
        ping_timeout=30,
        ws_protocols=None,
        root_path="",
        proxy_forwarded_address_header=None,
        proxy_forwarded_port_header=None,
        verbosity=1,
        websocket_handshake_timeout=5
    ):
        self.channel_layer = channel_layer
        self.consumer = consumer
        self.endpoints = endpoints or []
        if len(self.endpoints) == 0:
            raise UserWarning("No endpoints. This server will not listen on anything.")
        self.listeners = []
        self.signal_handlers = signal_handlers
        self.action_logger = action_logger
        self.http_timeout = http_timeout
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.proxy_forwarded_address_header = proxy_forwarded_address_header
        self.proxy_forwarded_port_header = proxy_forwarded_port_header
        # If they did not provide a websocket timeout, default it to the
        # channel layer's group_expiry value if present, or one day if not.
        self.websocket_timeout = websocket_timeout or getattr(channel_layer, "group_expiry", 86400)
        self.websocket_connect_timeout = websocket_connect_timeout
        self.websocket_handshake_timeout = websocket_handshake_timeout
        self.ws_protocols = ws_protocols
        self.root_path = root_path
        self.verbosity = verbosity

    def run(self):
        # Make the thread pool to run consumers in
        # TODO: Configurable numbers of threads
        self.pool = ThreadPool(name="consumers")
        # Make the mapping of consumer instances to consumer channels
        self.consumer_instances = {}
        # A set of current Twisted protocol instances to manage
        self.protocols = set()
        # Create process-local channel prefixes
        # TODO: Can we guarantee non-collision better?
        process_id = "".join(random.choice(string.ascii_letters) for i in range(10))
        self.consumer_channel_prefix = "daphne.%s!" % process_id
        # Make the factory
        self.http_factory = HTTPFactory(self)
        self.ws_factory = WebSocketFactory(self, protocols=self.ws_protocols, server='Daphne')
        self.ws_factory.setProtocolOptions(
            autoPingTimeout=self.ping_timeout,
            allowNullOrigin=True,
            openHandshakeTimeout=self.websocket_handshake_timeout
        )
        if self.verbosity <= 1:
            # Redirect the Twisted log to nowhere
            globalLogBeginner.beginLoggingTo([lambda _: None], redirectStandardIO=False, discardBuffer=True)
        else:
            globalLogBeginner.beginLoggingTo([STDLibLogObserver(__name__)])

        # Detect what Twisted features are enabled
        if http.H2_ENABLED:
            logger.info("HTTP/2 support enabled")
        else:
            logger.info("HTTP/2 support not enabled (install the http2 and tls Twisted extras)")

        # Kick off the various background loops
        reactor.callLater(0, self.backend_reader)
        reactor.callLater(2, self.timeout_checker)

        for socket_description in self.endpoints:
            logger.info("Listening on endpoint %s" % socket_description)
            # Twisted requires str on python2 (not unicode) and str on python3 (not bytes)
            ep = serverFromString(reactor, str(socket_description))
            self.listeners.append(ep.listen(self.http_factory))

        self.pool.start()
        reactor.addSystemEventTrigger("before", "shutdown", self.pool.stop)
        reactor.run(installSignalHandlers=self.signal_handlers)

    ### Protocol handling

    def add_protocol(self, protocol):
        if protocol in self.protocols:
            raise RuntimeError("Protocol %r was added to main list twice!" % protocol)
        self.protocols.add(protocol)

    def discard_protocol(self, protocol):
        self.protocols.discard(protocol)

    ### Internal event/message handling

    def create_consumer(self, protocol):
        """
        Creates a new consumer instance that fronts a Protocol instance
        for one of our supported protocols. Pass it the protocol,
        and it will work out the type, supply appropriate callables, and
        put it into the server's consumer pool.

        It returns the consumer channel name, which is how you should refer
        to the consumer instance.
        """
        # Make sure the protocol defines a consumer type
        assert protocol.consumer_type is not None
        # Make it a consumer channel name
        protocol_id = "".join(random.choice(string.ascii_letters) for i in range(10))
        consumer_channel = self.consumer_channel_prefix + protocol_id
        # Make an instance of the consumer
        consumer_instance = self.consumer(
            type=protocol.consumer_type,
            reply=lambda message: self.handle_reply(protocol, message),
            channel_layer=self.channel_layer,
            consumer_channel=consumer_channel,
        )
        # Assign it by channel and return it
        self.consumer_instances[consumer_channel] = consumer_instance
        return consumer_channel

    def handle_message(self, consumer_channel, message):
        """
        Schedules the application instance to handle the given message.
        """
        self.pool.callInThread(self.consumer_instances[consumer_channel], message)

    def handle_reply(self, protocol, message):
        """
        Schedules the reply to be handled by the protocol in the main thread
        """
        reactor.callFromThread(reactor.callLater, 0, protocol.handle_reply, message)

    ### External event/message handling

    def backend_reader(self):
        """
        Runs as an-often-as-possible task with the reactor, unless there was
        no result previously in which case we add a small delay.
        """
        channels = [self.consumer_channel_prefix]
        delay = 0
        # Quit if reactor is stopping
        if not reactor.running:
            logger.debug("Backend reader quitting due to reactor stop")
            return
        # Try to receive a message
        try:
            channel, message = self.channel_layer.receive(channels, block=False)
        except Exception as e:
            # Log the error and wait a bit to retry
            logger.error('Error trying to receive messages: %s' % e)
            delay = 5.00
        else:
            if channel:
                # Deal with the message
                try:
                    self.handle_message(channel, message)
                except Exception as e:
                    logger.error("Error handling external message: %s" % e)
            else:
                # If there's no messages, idle a little bit.
                delay = 0.05
        # We can't loop inside here as this is synchronous code.
        reactor.callLater(delay, self.backend_reader)

    ### Utility

    def timeout_checker(self):
        """
        Called periodically to enforce timeout rules on all connections.
        Also checks pings at the same time.
        """
        for protocol in self.protocols:
            protocol.check_timeouts()
        reactor.callLater(2, self.timeout_checker)

    def log_action(self, protocol, action, details):
        """
        Dispatches to any registered action logger, if there is one.
        """
        if self.action_logger:
            self.action_logger(protocol, action, details)
