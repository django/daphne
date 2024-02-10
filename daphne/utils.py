import importlib
import re

from twisted.web.http_headers import Headers

# Header name regex as per h11.
# https://github.com/python-hyper/h11/blob/a2c68948accadc3876dffcf979d98002e4a4ed27/h11/_abnf.py#L10-L21
HEADER_NAME_RE = re.compile(rb"[-!#$%&'*+.^_`|~0-9a-zA-Z]+")


def import_by_path(path):
    """
    Given a dotted/colon path, like project.module:ClassName.callable,
    returns the object at the end of the path.
    """
    module_path, object_path = path.split(":", 1)
    target = importlib.import_module(module_path)
    for bit in object_path.split("."):
        target = getattr(target, bit)
    return target


def header_value(headers, header_name):
    value = headers[header_name]
    if isinstance(value, list):
        value = value[0]
    return value.decode("utf-8")


def parse_x_forwarded_for(
    headers,
    address_header_name="X-Forwarded-For",
    port_header_name="X-Forwarded-Port",
    proto_header_name="X-Forwarded-Proto",
    original_addr=None,
    original_scheme=None,
):
    """
    Parses an X-Forwarded-For header and returns a host/port pair as a list.

    @param headers: The twisted-style object containing a request's headers
    @param address_header_name: The name of the expected host header
    @param port_header_name: The name of the expected port header
    @param proto_header_name: The name of the expected proto header
    @param original_addr: A host/port pair that should be returned if the headers are not in the request
    @param original_scheme: A scheme that should be returned if the headers are not in the request
    @return: A list containing a host (string) as the first entry and a port (int) as the second.
    """
    if not address_header_name:
        return original_addr, original_scheme

    # Convert twisted-style headers into dicts
    if isinstance(headers, Headers):
        headers = dict(headers.getAllRawHeaders())

    # Lowercase all header names in the dict
    headers = {name.lower(): values for name, values in headers.items()}

    # Make sure header names are bytes (values are checked in header_value)
    assert all(isinstance(name, bytes) for name in headers.keys())

    address_header_name = address_header_name.lower().encode("utf-8")
    result_addr = original_addr
    result_scheme = original_scheme
    if address_header_name in headers:
        address_value = header_value(headers, address_header_name)

        if "," in address_value:
            address_value = address_value.split(",")[0].strip()

        result_addr = [address_value, 0]

        if port_header_name:
            # We only want to parse the X-Forwarded-Port header if we also parsed the X-Forwarded-For
            # header to avoid inconsistent results.
            port_header_name = port_header_name.lower().encode("utf-8")
            if port_header_name in headers:
                port_value = header_value(headers, port_header_name)
                try:
                    result_addr[1] = int(port_value)
                except ValueError:
                    pass

        if proto_header_name:
            proto_header_name = proto_header_name.lower().encode("utf-8")
            if proto_header_name in headers:
                result_scheme = header_value(headers, proto_header_name)

    return result_addr, result_scheme
