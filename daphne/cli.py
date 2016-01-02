import sys
import argparse
import logging
import importlib
from .server import Server


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
            '-v',
            '--verbosity',
            type=int,
            help='How verbose to make the output',
            default=1,
        )
        self.parser.add_argument(
            'channel_layer',
            help='The ASGI channel layer instance to use as path.to.module:instance.path',
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
        # Import channel layer
        sys.path.insert(0, ".")
        module_path, object_path = args.channel_layer.split(":", 1)
        channel_layer = importlib.import_module(module_path)
        for bit in object_path.split("."):
            channel_layer = getattr(channel_layer, bit)
        # Run server
        logger.info(
            "Starting server on %s:%s, channel layer %s",
            args.host,
            args.port,
            args.channel_layer,
        )
        Server(
            channel_layer=channel_layer,
            host=args.host,
            port=args.port,
        ).run()
