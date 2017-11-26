from __future__ import unicode_literals

# This has to be done first as Twisted is import-order-sensitive with reactors
from twisted.internet import asyncioreactor
asyncioreactor.install()

import asyncio
import collections
import logging
import random
import string
import traceback
import warnings

from twisted.internet import reactor, defer
from twisted.internet.endpoints import serverFromString
from twisted.logger import globalLogBeginner, STDLibLogObserver
from twisted.web import http

from .http_protocol import HTTPFactory
from .ws_protocol import WebSocketFactory

logger = logging.getLogger(__name__)


class Server(object):

    def __init__(
        self,
        application,
        endpoints=None,
        signal_handlers=True,
        action_logger=None,
        http_timeout=120,
        websocket_timeout=86400,
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
        self.application = application
        self.endpoints = endpoints or []
        if not self.endpoints:
            raise UserWarning("No endpoints. This server will not listen on anything.")
        self.listeners = []
        self.signal_handlers = signal_handlers
        self.action_logger = action_logger
        self.http_timeout = http_timeout
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.proxy_forwarded_address_header = proxy_forwarded_address_header
        self.proxy_forwarded_port_header = proxy_forwarded_port_header
        self.websocket_timeout = websocket_timeout
        self.websocket_connect_timeout = websocket_connect_timeout
        self.websocket_handshake_timeout = websocket_handshake_timeout
        self.websocket_protocols = ws_protocols
        self.root_path = root_path
        self.verbosity = verbosity

    def run(self):
        # A set of current Twisted protocol instances to manage
        self.protocols = set()
        self.application_instances = {}
        # Make the factory
        self.http_factory = HTTPFactory(self)
        self.ws_factory = WebSocketFactory(self, protocols=self.websocket_protocols, server="Daphne")
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

        # Kick off the timeout loop
        reactor.callLater(1, self.application_checker)
        reactor.callLater(2, self.timeout_checker)

        for socket_description in self.endpoints:
            logger.info("Listening on endpoint %s", socket_description)
            ep = serverFromString(reactor, str(socket_description))
            self.listeners.append(ep.listen(self.http_factory))

        # Set the asyncio reactor's event loop as global
        # TODO: Should we instead pass the global one into the reactor?
        asyncio.set_event_loop(reactor._asyncioEventloop)

        # Verbosity 3 turns on asyncio debug to find those blocking yields
        if self.verbosity >= 3:
            asyncio.get_event_loop().set_debug(True)

        reactor.addSystemEventTrigger("before", "shutdown", self.kill_all_applications)
        reactor.run(installSignalHandlers=self.signal_handlers)

    def stop(self):
        """
        Force-stops the server.
        """
        reactor.stop()

    ### Protocol handling

    def add_protocol(self, protocol):
        if protocol in self.protocols:
            raise RuntimeError("Protocol %r was added to main list twice!" % protocol)
        self.protocols.add(protocol)

    def discard_protocol(self, protocol):
        # Ensure it's not in the protocol-tracking set
        self.protocols.discard(protocol)
        # Make sure any application future that's running is cancelled
        if protocol in self.application_instances:
            self.application_instances[protocol].cancel()
            del self.application_instances[protocol]

    ### Internal event/message handling

    def create_application(self, protocol, scope):
        """
        Creates a new application instance that fronts a Protocol instance
        for one of our supported protocols. Pass it the protocol,
        and it will work out the type, supply appropriate callables, and
        return you the application's input queue
        """
        # Make sure the protocol has not had another application made for it
        assert protocol not in self.application_instances
        # Make an instance of the application
        input_queue = asyncio.Queue()
        application_instance = self.application(scope=scope)
        # Run it, and stash the future for later checking
        self.application_instances[protocol] = asyncio.ensure_future(application_instance(
            receive=input_queue.get,
            send=lambda message: self.handle_reply(protocol, message),
        ), loop=asyncio.get_event_loop())
        return input_queue

    async def handle_reply(self, protocol, message):
        """
        Coroutine that jumps the reply message from asyncio to Twisted
        """
        reactor.callLater(0, protocol.handle_reply, message)

    ### Utility

    def application_checker(self):
        """
        Goes through the set of current application Futures and cleans up
        any that are done/prints exceptions for any that errored.
        """
        for protocol, application_instance in list(self.application_instances.items()):
            if application_instance.done():
                exception = application_instance.exception()
                if exception:
                    if isinstance(exception, KeyboardInterrupt):
                        # Protocol is asking the server to exit (likely during test)
                        self.stop()
                    else:
                        logging.error(
                            "Exception inside application: {}\n{}{}".format(
                                exception,
                                "".join(traceback.format_tb(
                                    exception.__traceback__,
                                )),
                                "  {}".format(exception),
                            )
                        )
                        protocol.handle_exception(exception)
                try:
                    del self.application_instances[protocol]
                except KeyError:
                    # The protocol might have already got here before us. That's fine.
                    pass
        reactor.callLater(1, self.application_checker)

    def kill_all_applications(self):
        """
        Kills all application coroutines before reactor exit.
        """
        # Send cancel to all coroutines
        wait_for = []
        for application_instance in self.application_instances.values():
            if not application_instance.done():
                application_instance.cancel()
                wait_for.append(application_instance)
        logging.info("Killed %i pending application instances", len(wait_for))
        # Make Twisted wait until they're all dead
        wait_deferred = defer.Deferred.fromFuture(asyncio.gather(*wait_for))
        wait_deferred.addErrback(lambda x: None)
        return wait_deferred

    def timeout_checker(self):
        """
        Called periodically to enforce timeout rules on all connections.
        Also checks pings at the same time.
        """
        for protocol in list(self.protocols):
            protocol.check_timeouts()
        reactor.callLater(2, self.timeout_checker)

    def log_action(self, protocol, action, details):
        """
        Dispatches to any registered action logger, if there is one.
        """
        if self.action_logger:
            self.action_logger(protocol, action, details)
