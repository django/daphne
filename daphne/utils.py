from twisted.web.http_headers import Headers


def header_value(headers, header_name):
    value = headers[header_name]
    if isinstance(value, list):
        value = value[0]
    # decode to urf-8 if value is bytes
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return value


def parse_x_forwarded_for(headers,
                          address_header_name='X-Forwarded-For',
                          port_header_name='X-Forwarded-Port',
                          proto_header_name='X-Forwarded-Proto',
                          original_addr=None,
                          original_scheme=None):
    """
    Parses an X-Forwarded-For header and returns a host/port pair as a list.

    @param headers: The twisted-style object containing a request's headers
    @param address_header_name: The name of the expected host header
    @param port_header_name: The name of the expected port header
    @param proto_header_name: The name of the expected protocol header
    @param original_addr: A host/port pair that should be returned if the headers are not in the request
    @param original_scheme: A scheme that should be returned if the headers are not in the request
    @return: A tuple containing a list [host (string), port (int)] as the first entry and a proto (string) as the second
    """
    if not address_header_name:
        return (original_addr, original_scheme)

    if isinstance(headers, Headers):
        # Convert twisted-style headers into a dict
        headers = dict(headers.getAllRawHeaders())
        # Lowercase all header keys
        headers = {name.lower(): values for name, values in headers.items()}
    else:
        # Lowercase (and encode to utf-8 where needed) non-twisted header keys
        headers = {name.lower() if isinstance(name, bytes) else name.lower().encode("utf-8"): values for name, values in headers.items()}

    address_header_name = address_header_name.lower().encode("utf-8")
    result_addr = original_addr
    result_scheme = original_scheme
    if address_header_name in headers:
        address_value = header_value(headers, address_header_name)

        if ',' in address_value:
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
