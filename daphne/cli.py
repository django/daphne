import sys
import argparse
import logging
import importlib
from .server import Server, build_endpoint_description_strings
from .access import AccessLogGenerator


logger = logging.getLogger(__name__)

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8000

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
            default=None,
        )
        self.parser.add_argument(
            '-b',
            '--bind',
            dest='host',
            help='The host/address to bind to',
            default=None,
        )
        self.parser.add_argument(
            '--websocket_timeout',
            type=int,
            help='max time websocket connected. -1 to infinite.',
            default=None,
        )
        self.parser.add_argument(
            '--websocket_connect_timeout',
            type=int,
            help='max time to refuse establishing connection. -1 to infinite',
            default=None,
        )
        self.parser.add_argument(
            '-u',
            '--unix-socket',
            dest='unix_socket',
            help='Bind to a UNIX socket rather than a TCP host/port',
            default=None,
        )
        self.parser.add_argument(
            '--fd',
            type=int,
            dest='file_descriptor',
            help='Bind to a file descriptor rather than a TCP host/port or named unix socket',
            default=None,
        )
        self.parser.add_argument(
            '-e',
            '--endpoint',
            dest='socket_strings',
            action='append',
            help='Use raw server strings passed directly to twisted',
            default=[],
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
            '--ping-timeout',
            type=int,
            help='The number of seconds before a WeSocket is closed if no response to a keepalive ping',
            default=30,
        )
        self.parser.add_argument(
            '--ws-protocol',
            nargs='*',
            dest='ws_protocols',
            help='The WebSocket protocols you wish to support',
            default=None,
        )
        self.parser.add_argument(
            '--root-path',
            dest='root_path',
            help='The setting for the ASGI root_path variable',
            default="",
        )
        self.parser.add_argument(
            '--proxy-headers',
            dest='proxy_headers',
            help='Enable parsing and using of X-Forwarded-For and X-Forwarded-Port headers and using that as the '
                 'client address',
            default=False,
            action='store_true',
        )
        self.parser.add_argument(
            '--force-sync',
            dest='force_sync',
            action='store_true',
            help='Force the server to use synchronous mode on its ASGI channel layer',
            default=False,
        )
        self.parser.add_argument(
            'channel_layer',
            help='The ASGI channel layer instance to use as path.to.module:instance.path',
        )

        self.server = None

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
            level={
                0: logging.WARN,
                1: logging.INFO,
                2: logging.DEBUG,
            }[args.verbosity],
            format="%(asctime)-15s %(levelname)-8s %(message)s",
        )
        # If verbosity is 1 or greater, or they told us explicitly, set up access log
        access_log_stream = None
        if args.access_log:
            if args.access_log == "-":
                access_log_stream = sys.stdout
            else:
                access_log_stream = open(args.access_log, "a", 1)
        elif args.verbosity >= 1:
            access_log_stream = sys.stdout
        # Import channel layer
        sys.path.insert(0, ".")
        module_path, object_path = args.channel_layer.split(":", 1)
        channel_layer = importlib.import_module(module_path)
        for bit in object_path.split("."):
            channel_layer = getattr(channel_layer, bit)

        if not any([args.host, args.port, args.unix_socket, args.file_descriptor, args.socket_strings]):
            # no advanced binding options passed, patch in defaults
            args.host = DEFAULT_HOST
            args.port = DEFAULT_PORT
        elif args.host and not args.port:
            args.port = DEFAULT_PORT
        elif args.port and not args.host:
            args.host = DEFAULT_HOST

        # build endpoint description strings from (optional) cli arguments
        endpoints = build_endpoint_description_strings(
            host=args.host,
            port=args.port,
            unix_socket=args.unix_socket,
            file_descriptor=args.file_descriptor
        )
        endpoints = sorted(
            args.socket_strings + endpoints
        )
        logger.info(
            'Starting server at %s, channel layer %s.' %
            (', '.join(endpoints), args.channel_layer)
        )

        self.server = Server(
            channel_layer=channel_layer,
            endpoints=endpoints,
            http_timeout=args.http_timeout,
            ping_interval=args.ping_interval,
            ping_timeout=args.ping_timeout,
            websocket_timeout=args.websocket_timeout,
            websocket_connect_timeout=args.websocket_connect_timeout,
            action_logger=AccessLogGenerator(access_log_stream) if access_log_stream else None,
            ws_protocols=args.ws_protocols,
            root_path=args.root_path,
            verbosity=args.verbosity,
            proxy_forwarded_address_header='X-Forwarded-For' if args.proxy_headers else None,
            proxy_forwarded_port_header='X-Forwarded-Port' if args.proxy_headers else None,
            force_sync=args.force_sync,
        )
        self.server.run()
