from __future__ import unicode_literals
import six
from six.moves.urllib import parse

from asgiref.inmemory import ChannelLayer
from twisted.test import proto_helpers

from daphne.http_protocol import HTTPFactory


def message_for_request(method, path, params=None, headers=None, body=None):
    """
    Constructs a HTTP request according to the given parameters, runs
    that through daphne and returns the emitted channel message.
    """
    request = _build_request(method, path, params, headers, body)
    return _run_through_daphne(request, 'http.request')


def _build_request(method, path, params=None, headers=None, body=None):
    """
    Takes request parameters and returns a byte string of a valid HTTP/1.1 request.

    We really shouldn't manually build a HTTP request, and instead try to capture
    what e.g. urllib or requests would do. But that is non-trivial, so meanwhile
    we hope that our request building doesn't mask any errors.

    This code is messy, because urllib behaves rather different between Python 2
    and 3. Readability is further obstructed by the fact that Python 3.4 doesn't
    support % formatting for bytes, so we need to concat everything.
    If we run into more issues with this, the python-future library has a backport
    of Python 3's urllib.

    :param method: ASCII string of HTTP method.
    :param path: unicode string of URL path.
    :param params: List of two-tuples of bytestrings, ready for consumption for
                   urlencode. Encode to utf8 if necessary.
    :param headers: List of two-tuples ASCII strings of HTTP header, value.
    :param body: ASCII string of request body.

    ASCII string is short for a unicode string containing only ASCII characters,
    or a byte string with ASCII encoding.
    """
    if headers is None:
        headers = []
    else:
        headers = headers[:]

    if six.PY3:
        quoted_path = parse.quote(path)
        if params:
            quoted_path += '?' + parse.urlencode(params)
        quoted_path = quoted_path.encode('ascii')
    else:
        quoted_path = parse.quote(path.encode('utf8'))
        if params:
            quoted_path += b'?' + parse.urlencode(params)

    request = method.encode('ascii') + b' ' + quoted_path + b" HTTP/1.1\r\n"
    for k, v in headers:
        request += k.encode('ascii') + b': ' + v.encode('ascii') + b"\r\n"

    request += b'\r\n'

    if body:
        request += body.encode('ascii')

    return request


def _run_through_daphne(request, channel_name):
    """
    Returns Daphne's channel message for a given request.

    This helper requires a fair bit of scaffolding and can certainly be improved,
    but it works for now.
    """
    channel_layer = ChannelLayer()
    factory = HTTPFactory(channel_layer)
    proto = factory.buildProtocol(('127.0.0.1', 0))
    tr = proto_helpers.StringTransport()
    proto.makeConnection(tr)
    proto.dataReceived(request)
    _, message = channel_layer.receive([channel_name])
    return message


def content_length_header(body):
    """
    Returns an appropriate Content-Length HTTP header for a given body.
    """
    return 'Content-Length', six.text_type(len(body))
