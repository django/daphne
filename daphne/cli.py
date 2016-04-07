import sys
import argparse
import logging
import importlib
from .server import Server
from .access import AccessLogGenerator


logger = logging.getLogger(__name__)


class CommandLineInterface(object):
    """
    Acts as the main CLI entry point for running the server.
    """

    description = "Django HTTP/WebSocket server"

    def __init__(self):
        self.parser = argparse.ArgumentParser(
            description=self.description,
        )
        self.parser.add_argument(
            '-p',
            '--port',
            type=int,
            help='Port number to listen on',
            default=8000,
        )
        self.parser.add_argument(
            '-b',
            '--bind',
            dest='host',
            help='The host/address to bind to',
            default="127.0.0.1",
        )
        self.parser.add_argument(
            '-u',
            '--unix-socket',
            dest='unix_socket',
            help='Bind to a UNIX socket rather than a TCP host/port',
            default=None,
        )
        self.parser.add_argument(
            '-v',
            '--verbosity',
            type=int,
            help='How verbose to make the output',
            default=1,
        )
        self.parser.add_argument(
            '-t',
            '--http-timeout',
            type=int,
            help='How long to wait for worker server before timing out HTTP connections',
            default=120,
        )
        self.parser.add_argument(
            '--access-log',
            help='Where to write the access log (- for stdout, the default for verbosity=1)',
            default=None,
        )
        self.parser.add_argument(
            '--ping-interval',
            type=int,
            help='The number of seconds a WebSocket must be idle before a keepalive ping is sent',
            default=20,
        )
        self.parser.add_argument(
            'channel_layer',
            help='The ASGI channel layer instance to use as path.to.module:instance.path',
        )
        self.parser.add_argument(
            '--ws-protocol',
            nargs='*',
            dest='ws_protocols',
            help='The WebSocket protocols you wish to support',
            default=None,
        )

    @classmethod
    def entrypoint(cls):
        """
        Main entrypoint for external starts.
        """
        cls().run(sys.argv[1:])

    def run(self, args):
        """
        Pass in raw argument list and it will decode them
        and run the server.
        """
        # Decode args
        args = self.parser.parse_args(args)
        # Set up logging
        logging.basicConfig(
            level = {
                0: logging.WARN,
                1: logging.INFO,
                2: logging.DEBUG,
            }[args.verbosity],
            format = "%(asctime)-15s %(levelname)-8s %(message)s" ,
        )
        # If verbosity is 1 or greater, or they told us explicitly, set up access log
        access_log_stream = None
        if args.access_log:
            if args.access_log == "-":
                access_log_stream = sys.stdout
            else:
                access_log_stream = open(args.access_log, "a")
        elif args.verbosity >= 1:
            access_log_stream = sys.stdout
        # Import channel layer
        sys.path.insert(0, ".")
        module_path, object_path = args.channel_layer.split(":", 1)
        channel_layer = importlib.import_module(module_path)
        for bit in object_path.split("."):
            channel_layer = getattr(channel_layer, bit)
        # Run server
        logger.info(
            "Starting server at %s, channel layer %s",
            (args.unix_socket if args.unix_socket else "%s:%s" % (args.host, args.port)),
            args.channel_layer,
        )
        Server(
            channel_layer=channel_layer,
            host=args.host,
            port=args.port,
            unix_socket=args.unix_socket,
            http_timeout=args.http_timeout,
            ping_interval=args.ping_interval,
            action_logger=AccessLogGenerator(access_log_stream) if access_log_stream else None,
            ws_protocols=args.ws_protocols,
        ).run()
