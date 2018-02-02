

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
    if host and port is not None:
        host = host.strip("[]").replace(":", "\:")
        socket_descriptions.append("tcp:port=%d:interface=%s" % (int(port), host))
    elif any([host, port]):
        raise ValueError("TCP binding requires both port and host kwargs.")

    if unix_socket:
        socket_descriptions.append("unix:%s" % unix_socket)

    if file_descriptor is not None:
        socket_descriptions.append("fd:fileno=%d" % int(file_descriptor))

    return socket_descriptions
