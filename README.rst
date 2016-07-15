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


If you intend to run daphne behind a proxy server you can use UNIX
sockets to communicate between the two::

    daphne -u /tmp/daphne.sock django_project.asgi:channel_layer


If daphne is being run inside a process manager such as
`Circus <https://github.com/circus-tent/circus/>`_ you might
want it to bind to a file descriptor passed down from a parent process.
To achieve this you can use the --fd flag::

    daphne --fd 5 django_project.asgi:channel_layer

To see all available command line options run daphne with the *-h* flag.

Root Path (SCRIPT_NAME)
-----------------------

In order to set the root path for Daphne, which is the equivalent of the
WSGI ``SCRIPT_NAME`` setting, you have two options:

* Pass a header value ``Daphne-Root-Path``, with the desired root path as a
  URLencoded ASCII value. This header will not be passed down to applications.

* Set the ``--root-path`` commandline option with the desired root path as a
  URLencoded ASCII value.

The header takes precedence if both are set. As with ``SCRIPT_ALIAS``, the value
should start with a slash, but not end with one; for example::

    daphne --root-path=/forum django_project.asgi:channel_layer
