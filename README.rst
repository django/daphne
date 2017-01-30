daphne
======

.. image:: https://api.travis-ci.org/django/daphne.svg
    :target: https://travis-ci.org/django/daphne
    
.. image:: https://img.shields.io/pypi/v/daphne.svg
    :target: https://pypi.python.org/pypi/daphne

Daphne is a HTTP, HTTP2 and WebSocket protocol server for
`ASGI <https://channels.readthedocs.io/en/latest/asgi.html>`_, and developed
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


If you want more control over the port/socket bindings you can fall back to
using `twisted's endpoint description strings
<http://twistedmatrix.com/documents/current/api/twisted.internet.endpoints.html#serverFromString>`_
by using the `--endpoint (-e)` flag, which can be used multiple times.
This line would start a SSL server on port 443, assuming that `key.pem` and `crt.pem`
exist in the current directory (requires pyopenssl to be installed)::

    daphne -e ssl:443:privateKey=key.pem:certKey=crt.pem django_project.asgi:channel_layer


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

Dependencies
------------

All Channels projects currently support Python 2.7, 3.4 and 3.5. `daphne` requires Twisted 16.0 or
greater.

Contributing
------------

Please refer to the
`main Channels contributing docs <https://github.com/django/channels/blob/master/CONTRIBUTING.rst>`_.
That also contains advice on how to set up the development environment and run the tests.


Maintenance and Security
------------------------

To report security issues, please contact security@djangoproject.com. For GPG
signatures and more security process information, see
https://docs.djangoproject.com/en/dev/internals/security/.

To report bugs or request new features, please open a new GitHub issue.

This repository is part of the Channels project. For the shepherd and maintenance team, please see the
`main Channels readme <https://github.com/django/channels/blob/master/README.rst>`_.
