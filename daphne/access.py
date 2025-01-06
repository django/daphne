import logging

logger = logging.getLogger(__name__)


class AccessLogGenerator:
    """
    Object that implements the Daphne "action logger" internal interface in
    order to provide an access log in something resembling NCSA format.
    """

    def __init__(self, stream):
        if stream:
            logger.propagate = False
            handler = logging.StreamHandler(stream)
            formatter = logging.Formatter(
                '%(host)s %(ident)s %(user)s [%(asctime)s] "%(message)s" '
                "%(status)s %(length)s",
                "%d/%b/%Y:%H:%M:%S"
            )
            handler.setFormatter(fmt=formatter)
            logger.addHandler(handler)


    def __call__(self, protocol, action, details):
        """
        Called when an action happens; use it to generate log entries.
        """
        # HTTP requests
        if protocol == "http" and action == "complete":
            self.write_entry(
                host=details["client"],
                request="%(method)s" % details,
                details="%(path)s" % details,
                status=details["status"],
                length=details["size"],
            )
        # Websocket requests
        elif protocol == "websocket" and action == "connecting":
            self.write_entry(
                host=details["client"],
                request="WSCONNECTING",
                details="%(path)s" % details,
            )
        elif protocol == "websocket" and action == "rejected":
            self.write_entry(
                host=details["client"],
                request="WSREJECT",
                details="%(path)s" % details,
            )
        elif protocol == "websocket" and action == "connected":
            self.write_entry(
                host=details["client"],
                request="WSCONNECT",
                details="%(path)s" % details,
            )
        elif protocol == "websocket" and action == "disconnected":
            self.write_entry(
                host=details["client"],
                request="WSDISCONNECT",
                details="%(path)s" % details,
            )

    def write_entry(
        self, host, request, details, status=None, length=None, ident=None, user=None
    ):
        """
        Writes an access log.  If a file is specified, an NCSA-style entry to the log file 
        (some liberty is taken with what the entries are for non-HTTP).  The format can be 
        overriden with logging configuration for 'daphne.access'
        """

        logger.info(
            "%s %s",
            request,
            details,
            extra={
                "host": host,
                "request": request,
                "details": details,
                "ident": ident or "-",
                "user": user or "-",
                "status": status or "-",
                "length": length or "-",
            },
        )
