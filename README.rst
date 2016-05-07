daphne
======

.. image:: https://api.travis-ci.org/andrewgodwin/daphne.svg
    :target: https://travis-ci.org/andrewgodwin/daphne
    
.. image:: https://img.shields.io/pypi/v/daphne.svg
    :target: https://pypi.python.org/pypi/daphne

Daphne is a HTTP, HTTP2 and WebSocket protocol server for
`ASGI <http://channels.readthedocs.org/en/latest/asgi.html>`_, and developed
to power Django Channels.

It supports automatic negotiation of protocols; there's no need for URL
prefixing to determine WebSocket endpoints versus HTTP endpoints.

Running
-------

Simply point Daphne to your ASGI channel layer instance, and optionally
set a bind address and port (defaults to localhost, port 8000)::

    daphne -b 0.0.0.0 -p 8001 django_project.asgi:channel_layer
