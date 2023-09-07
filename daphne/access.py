import datetime


class AccessLogGenerator:
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
        if protocol == "http" and action == "complete":
            self.write_entry(
                host=details["client"],
                date=datetime.datetime.now(),
                request=f"{details['method']} {details['path']}",
                status=details.get("status", "-"),
                length=details.get("size", "-"),
            )
        elif protocol == "websocket":
            message = f"WS{action.upper()} {details.get('path', '')}"
            self.write_entry(
                host=details["client"],
                date=datetime.datetime.now(),
                request=message,
            )

    def write_entry(
        self, host, date, request, status="-", length="-", ident="-", user="-"
    ):
        """
        Writes an NCSA-style entry to the log file (some liberty is taken with
        what the entries are for non-HTTP)
        """
        formatted_date = date.strftime("%d/%b/%Y:%H:%M:%S")
        self.stream.write(
            f'{host} {ident} {user} [{formatted_date}] "{request}" {status} {length}\n'
        )
