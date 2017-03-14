"""
Assorted Hypothesis strategies useful for generating HTTP requests and responses
"""
from __future__ import unicode_literals
from six.moves.urllib import parse
import string

from hypothesis import strategies

HTTP_METHODS = ['OPTIONS', 'GET', 'HEAD', 'POST', 'PUT', 'DELETE', 'TRACE', 'CONNECT']

# Unicode characters of the "Letter" category
letters = strategies.characters(whitelist_categories=('Lu', 'Ll', 'Lt', 'Lm', 'Lo', 'Nl'))


def http_method():
    return strategies.sampled_from(HTTP_METHODS)


def http_path():
    """
    Returns a URL path (not encoded).
    """
    alphabet = string.ascii_letters + string.digits + '-._~/'
    return strategies.text(min_size=0, max_size=255, alphabet=alphabet).map(lambda s: '/' + s)


def http_body():
    """
    Returns random printable ASCII characters. This may be exceeding what HTTP allows,
    but seems to not cause an issue so far.
    """
    return strategies.text(alphabet=string.printable, min_size=0, average_size=600, max_size=1500)


def valid_bidi(value):
    """
    Rejects strings which nonsensical Unicode text direction flags.

    Relying on random Unicode characters means that some combinations don't make sense, from a
    direction of text point of view. This little helper just rejects those.
    """
    try:
        value.encode('idna')
    except UnicodeError:
        return False
    else:
        return True


def _domain_label():
    return strategies.text(
        alphabet=letters, min_size=1, average_size=6, max_size=63).filter(valid_bidi)


def international_domain_name():
    """
    Returns a byte string of a domain name, IDNA-encoded.
    """
    return strategies.lists(
        _domain_label(), min_size=2, average_size=2).map(lambda s: ('.'.join(s)).encode('idna'))


def _query_param():
    return strategies.text(alphabet=letters, min_size=1, average_size=10, max_size=255).\
        map(lambda s: s.encode('utf8'))


def query_params():
    """
    Returns a list of two-tuples byte strings, ready for encoding with urlencode.
    We're aiming for a total length of a URL below 2083 characters, so this strategy
    ensures that the total urlencoded query string is not longer than 1500 characters.
    """
    return strategies.lists(
        strategies.tuples(_query_param(), _query_param()), min_size=0, average_size=5).\
        filter(lambda x: len(parse.urlencode(x)) < 1500)


def header_name():
    """
    Strategy returning something that looks like a HTTP header field

    https://en.wikipedia.org/wiki/List_of_HTTP_header_fields suggests they are between 4
    and 20 characters long
    """
    return strategies.text(
        alphabet=string.ascii_letters + string.digits + '-', min_size=1, max_size=30)


def header_value():
    """
    Strategy returning something that looks like a HTTP header value

    "For example, the Apache 2.3 server by default limits the size of each field to 8190 bytes"
    https://en.wikipedia.org/wiki/List_of_HTTP_header_fields
    """
    return strategies.text(
        alphabet=string.ascii_letters + string.digits + string.punctuation + ' /t',
        min_size=1, average_size=40, max_size=8190).filter(lambda s: len(s.encode('utf8')) < 8190)


def headers():
    """
    Strategy returning a list of tuples, containing HTTP header fields and their values.

    "[Apache 2.3] there can be at most 100 header fields in a single request."
    https://en.wikipedia.org/wiki/List_of_HTTP_header_fields
    """
    return strategies.lists(
        strategies.tuples(header_name(), header_value()),
        min_size=0, average_size=10, max_size=100)
