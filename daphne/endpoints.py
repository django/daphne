from abc import ABC, abstractmethod


class Endpoint(ABC):
    @abstractmethod
    def parse(self, options):
        pass


class TCPEndpoint(Endpoint):
    def parse(self, options):
        if options.get("port") and options.get("host"):
            host = options["host"].strip("[]").replace(":", r"\:")
            return f"tcp:port={int(options['port'])}:interface={host}"
        elif options.get("port") or options.get("host"):
            raise ValueError("TCP binding requires both port and host kwargs.")
        return None


class UNIXEndpoint(Endpoint):
    def parse(self, options):
        if options.get("unix_socket"):
            return f"unix:{options['unix_socket']}"
        return None


class FileDescriptorEndpoint(Endpoint):
    def parse(self, options):
        if options.get("file_descriptor") is not None:
            return f"fd:fileno={int(options['file_descriptor'])}"
        return None


endpoint_parsers = [TCPEndpoint(), UNIXEndpoint(), FileDescriptorEndpoint()]


def build_endpoint_description_strings(**kwargs):
    """
    Build a list of twisted endpoint description strings that the server will listen on.
    This is to streamline the generation of twisted endpoint description strings from easier
    to use command line args such as host, port, unix sockets etc.
    """
    socket_descriptions = []
    for parser in endpoint_parsers:
        description = parser.parse(kwargs)
        if description:
            socket_descriptions.append(description)
    return socket_descriptions
