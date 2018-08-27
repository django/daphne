import datetime


class AccessLogGenerator(object):
    """
    Object that implements the Daphne "action logger" internal interface in
    order to provide an access log in something resembling NCSA format.
    """

    def __init__(self, stream):
        self.stream = stream

    def __call__(self, protocol, action, details):
        """
        Called when an action happens; use it to generate log entries.
        """
        # HTTP requests
        if protocol == "http" and action == "complete":
            self.write_entry(
                host=details["client"],
                date=datetime.datetime.now(),
                request="%(method)s %(path)s" % details,
                status=details["status"],
                length=details["size"],
            )
        # Websocket requests
        elif protocol == "websocket" and action == "connecting":
            self.write_entry(
                host=details["client"],
                date=datetime.datetime.now(),
                request="WSCONNECTING %(path)s" % details,
            )
        elif protocol == "websocket" and action == "rejected":
            self.write_entry(
                host=details["client"],
                date=datetime.datetime.now(),
                request="WSREJECT %(path)s" % details,
            )
        elif protocol == "websocket" and action == "connected":
            self.write_entry(
                host=details["client"],
                date=datetime.datetime.now(),
                request="WSCONNECT %(path)s" % details,
            )
        elif protocol == "websocket" and action == "disconnected":
            self.write_entry(
                host=details["client"],
                date=datetime.datetime.now(),
                request="WSDISCONNECT %(path)s" % details,
            )

    def write_entry(
        self, host, date, request, status=None, length=None, ident=None, user=None
    ):
        """
        Writes an NCSA-style entry to the log file (some liberty is taken with
        what the entries are for non-HTTP)
        """
        self.stream.write(
            '%s %s %s [%s] "%s" %s %s\n'
            % (
                host,
                ident or "-",
                user or "-",
                date.strftime("%d/%b/%Y:%H:%M:%S"),
                request,
                status or "-",
                length or "-",
            )
        )
