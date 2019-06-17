import string
from urllib import parse

from hypothesis import strategies

HTTP_METHODS = ["OPTIONS", "GET", "HEAD", "POST", "PUT", "DELETE", "TRACE", "CONNECT"]

# Unicode characters of the "Letter" category
letters = strategies.characters(
    whitelist_categories=("Lu", "Ll", "Lt", "Lm", "Lo", "Nl")
)


def http_method():
    return strategies.sampled_from(HTTP_METHODS)


def _http_path_portion():
    alphabet = string.ascii_letters + string.digits + "-._~"
    return strategies.text(min_size=1, max_size=128, alphabet=alphabet)


def http_path():
    """
    Returns a URL path (not encoded).
    """
    return strategies.lists(_http_path_portion(), min_size=0, max_size=10).map(
        lambda s: "/" + "/".join(s)
    )


def http_body():
    """
    Returns random binary body data.
    """
    return strategies.binary(min_size=0, max_size=1500)


def valid_bidi(value):
    """
    Rejects strings which nonsensical Unicode text direction flags.

    Relying on random Unicode characters means that some combinations don't make sense, from a
    direction of text point of view. This little helper just rejects those.
    """
    try:
        value.encode("idna")
    except UnicodeError:
        return False
    else:
        return True


def _domain_label():
    return strategies.text(alphabet=letters, min_size=1, max_size=63).filter(valid_bidi)


def international_domain_name():
    """
    Returns a byte string of a domain name, IDNA-encoded.
    """
    return strategies.lists(_domain_label(), min_size=2).map(
        lambda s: (".".join(s)).encode("idna")
    )


def _query_param():
    return strategies.text(alphabet=letters, min_size=1, max_size=255).map(
        lambda s: s.encode("utf8")
    )


def query_params():
    """
    Returns a list of two-tuples byte strings, ready for encoding with urlencode.
    We're aiming for a total length of a URL below 2083 characters, so this strategy
    ensures that the total urlencoded query string is not longer than 1500 characters.
    """
    return strategies.lists(
        strategies.tuples(_query_param(), _query_param()), min_size=0
    ).filter(lambda x: len(parse.urlencode(x)) < 1500)


def header_name():
    """
    Strategy returning something that looks like a HTTP header field

    https://en.wikipedia.org/wiki/List_of_HTTP_header_fields suggests they are between 4
    and 20 characters long
    """
    return strategies.text(
        alphabet=string.ascii_letters + string.digits + "-", min_size=1, max_size=30
    ).map(lambda s: s.encode("utf-8"))


def header_value():
    """
    Strategy returning something that looks like a HTTP header value

    "For example, the Apache 2.3 server by default limits the size of each field to 8190 bytes"
    https://en.wikipedia.org/wiki/List_of_HTTP_header_fields
    """
    return (
        strategies.text(
            alphabet=string.ascii_letters
            + string.digits
            + string.punctuation.replace(",", "")
            + " /t",
            min_size=1,
            max_size=8190,
        )
        .map(lambda s: s.encode("utf-8"))
        .filter(lambda s: len(s) < 8190)
    )


def headers():
    """
    Strategy returning a list of tuples, containing HTTP header fields and their values.

    "[Apache 2.3] there can be at most 100 header fields in a single request."
    https://en.wikipedia.org/wiki/List_of_HTTP_header_fields
    """
    return strategies.lists(
        strategies.tuples(header_name(), header_value()), min_size=0, max_size=100
    )
