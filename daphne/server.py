from __future__ import unicode_literals

import logging
import random
import string
import warnings

from twisted.internet import reactor, defer
from twisted.internet.endpoints import serverFromString
from twisted.logger import globalLogBeginner, STDLibLogObserver
from twisted.web import http

from .http_protocol import HTTPFactory

logger = logging.getLogger(__name__)


class Server(object):

    def __init__(
        self,
        channel_layer,
        host=None,
        port=None,
        endpoints=None,
        unix_socket=None,
        file_descriptor=None,
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
        force_sync=False,
        verbosity=1
    ):
        self.channel_layer = channel_layer
        self.endpoints = endpoints or []

        if any([host, port, unix_socket, file_descriptor]):
            warnings.warn('''
                The host/port/unix_socket/file_descriptor keyword arguments to %s are deprecated.
            ''' % self.__class__.__name__, DeprecationWarning)
            # build endpoint description strings from deprecated kwargs
            self.endpoints = sorted(self.endpoints + build_endpoint_description_strings(
                host=host,
                port=port,
                unix_socket=unix_socket,
                file_descriptor=file_descriptor
            ))

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
        self.ws_protocols = ws_protocols
        self.root_path = root_path
        self.force_sync = force_sync
        self.verbosity = verbosity

    def run(self):
        # Create process-local channel prefixes
        # TODO: Can we guarantee non-collision better?
        process_id = "".join(random.choice(string.ascii_letters) for i in range(10))
        self.send_channel = "daphne.response.%s!" % process_id
        # Make the factory
        self.factory = HTTPFactory(
            self.channel_layer,
            action_logger=self.action_logger,
            send_channel=self.send_channel,
            timeout=self.http_timeout,
            websocket_timeout=self.websocket_timeout,
            websocket_connect_timeout=self.websocket_connect_timeout,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
            ws_protocols=self.ws_protocols,
            root_path=self.root_path,
            proxy_forwarded_address_header=self.proxy_forwarded_address_header,
            proxy_forwarded_port_header=self.proxy_forwarded_port_header
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

        if "twisted" in self.channel_layer.extensions and not self.force_sync:
            logger.info("Using native Twisted mode on channel layer")
            reactor.callLater(0, self.backend_reader_twisted)
        else:
            logger.info("Using busy-loop synchronous mode on channel layer")
            reactor.callLater(0, self.backend_reader_sync)
        reactor.callLater(2, self.timeout_checker)

        for socket_description in self.endpoints:
            logger.info("Listening on endpoint %s" % socket_description)
            # Twisted requires str on python2 (not unicode) and str on python3 (not bytes)
            ep = serverFromString(reactor, str(socket_description))
            self.listeners.append(ep.listen(self.factory))

        reactor.run(installSignalHandlers=self.signal_handlers)

    def backend_reader_sync(self):
        """
        Runs as an-often-as-possible task with the reactor, unless there was
        no result previously in which case we add a small delay.
        """
        channels = [self.send_channel]
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
                    self.factory.dispatch_reply(channel, message)
                except Exception as e:
                    logger.error("HTTP/WS send decode error: %s" % e)
            else:
                # If there's no messages, idle a little bit.
                delay = 0.05
        # We can't loop inside here as this is synchronous code.
        reactor.callLater(delay, self.backend_reader_sync)

    @defer.inlineCallbacks
    def backend_reader_twisted(self):
        """
        Runs as an-often-as-possible task with the reactor, unless there was
        no result previously in which case we add a small delay.
        """
        channels = [self.send_channel]
        while True:
            if not reactor.running:
                logging.debug("Backend reader quitting due to reactor stop")
                return
            try:
                channel, message = yield self.channel_layer.receive_twisted(channels)
            except Exception as e:
                logger.error('Error trying to receive messages: %s' % e)
                yield self.sleep(5.00)
            else:
                # Deal with the message
                if channel:
                    try:
                        self.factory.dispatch_reply(channel, message)
                    except Exception as e:
                        logger.error("HTTP/WS send decode error: %s" % e)

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


def build_endpoint_description_strings(
    host=None,
    port=None,
    unix_socket=None,
    file_descriptor=None
    ):
    """
    Build a list of twisted endpoint description strings that the server will listen on.
    This is to streamline the generation of twisted endpoint description strings from easier
    to use command line args such as host, port, unix sockets etc.
    """
    socket_descriptions = []
    if host and port:
        host = host.strip('[]').replace(':', '\:')
        socket_descriptions.append('tcp:port=%d:interface=%s' % (int(port), host))
    elif any([host, port]):
        raise ValueError('TCP binding requires both port and host kwargs.')

    if unix_socket:
        socket_descriptions.append('unix:%s' % unix_socket)

    if file_descriptor:
        socket_descriptions.append('fd:fileno=%d' % int(file_descriptor))

    return socket_descriptions
