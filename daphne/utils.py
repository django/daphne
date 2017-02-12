from twisted.web.http_headers import Headers


def parse_x_forwarded_for(headers,
                          address_header_name='X-Forwarded-For',
                          port_header_name='X-Forwarded-Port',
                          original=None):
    """
    Parses an X-Forwarded-For header and returns a host/port pair as a list.

    @param headers: The twisted-style object containing a request's headers
    @param address_header_name: The name of the expected host header
    @param port_header_name: The name of the expected port header
    @param original: A host/port pair that should be returned if the headers are not in the request
    @return: A list containing a host (string) as the first entry and a port (int) as the second.
    """
    if not address_header_name:
        return original

    # Convert twisted-style headers into dicts
    if isinstance(headers, Headers):
        headers = dict(headers.getAllRawHeaders())

    # Lowercase all header names in the dict
    headers = {name.lower(): values for name, values in headers.items()}

    address_header_name = address_header_name.lower().encode("utf-8")
    result = original
    if address_header_name in headers:
        address_value = headers[address_header_name][0].decode("utf-8")

        if ',' in address_value:
            address_value = address_value.split(",")[-1].strip()

        if ':' in address_value:
            address_host, address_port = address_value.split(':')
            result = [address_host, 0]
            try:
                result[1] = int(address_port)
            except ValueError:
                pass
        else:
            result = [address_value, 0]

        if port_header_name:
            # We only want to parse the X-Forwarded-Port header if we also parsed the X-Forwarded-For
            # header to avoid inconsistent results.
            port_header_name = port_header_name.lower().encode("utf-8")
            if port_header_name in headers:
                port_value = headers[port_header_name][0].decode("utf-8")
                try:
                    result[1] = int(port_value)
                except ValueError:
                    pass

    return result
