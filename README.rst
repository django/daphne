daphne
======

.. image:: https://img.shields.io/pypi/v/daphne.svg
    :target: https://pypi.python.org/pypi/daphne

Daphne is a HTTP, HTTP2 and WebSocket protocol server for
`ASGI <https://github.com/django/asgiref/blob/main/specs/asgi.rst>`_ and
`ASGI-HTTP <https://github.com/django/asgiref/blob/main/specs/www.rst>`_,
developed to power Django Channels.

It supports automatic negotiation of protocols; there's no need for URL
prefixing to determine WebSocket endpoints versus HTTP endpoints.


Running
-------

Simply point Daphne to your ASGI application, and optionally
set a bind address and port (defaults to localhost, port 8000)::

    daphne -b 0.0.0.0 -p 8001 django_project.asgi:application

If you intend to run daphne behind a proxy server you can use UNIX
sockets to communicate between the two::

    daphne -u /tmp/daphne.sock django_project.asgi:application

If daphne is being run inside a process manager, you might
want it to bind to a file descriptor passed down from a parent process.
To achieve this you can use the --fd flag::

    daphne --fd 5 django_project.asgi:application

If you want more control over the port/socket bindings you can fall back to
using `twisted's endpoint description strings
<http://twistedmatrix.com/documents/current/api/twisted.internet.endpoints.html#serverFromString>`_
by using the `--endpoint (-e)` flag, which can be used multiple times.
This line would start a SSL server on port 443, assuming that `key.pem` and `crt.pem`
exist in the current directory (requires pyopenssl to be installed)::

    daphne -e ssl:443:privateKey=key.pem:certKey=crt.pem django_project.asgi:application

Endpoints even let you use the ``txacme`` endpoint syntax to get automatic certificates
from Let's Encrypt, which you can read more about at http://txacme.readthedocs.io/en/stable/.

To see all available command line options run daphne with the ``-h`` flag.


Django development server
-------------------------

Add ``"daphne"`` before ``"django.contrib.staticfiles"`` in ``INSTALLED_APPS``
and set ``ASGI_APPLICATION`` to your project's ASGI application. Daphne then
provides Django's ``manage.py runserver`` command, including autoreloading,
system checks, migration warnings and development static files.

The development server uses plain HTTP by default. To opt into HTTPS, supply
both a PEM certificate and its matching, unencrypted PEM private key::

    python manage.py runserver --certfile localhost.pem --keyfile localhost-key.pem

The usual address and port arguments still apply::

    python manage.py runserver 127.0.0.1:8443 --certfile localhost.pem --keyfile localhost-key.pem

This starts one HTTPS listener and displays an ``https://`` startup URL. The
configured ``ASGI_APPLICATION``, including middleware and WebSocket routing,
continues to handle requests. Secure WebSockets use ``wss://``. ``--noreload``,
``--nostatic`` and ``--insecure`` retain their usual meanings; ``--insecure``
controls static-file serving when ``DEBUG=False``, not TLS verification.
Certificate options cannot be combined with ``--noasgi``.

Generate certificates and install any development CA trust outside Daphne,
using a tool such as ``mkcert``. The certificate must cover the hostname or IP
address used by your client. Paths are relative to the command's working
directory; quote paths containing spaces. Daphne does not generate, renew or
trust certificates. Restart runserver after replacing certificate files;
certificate changes alone do not trigger Python's autoreloader.

These options use Twisted's existing ``ssl:`` endpoint support internally.
Runserver keeps Django's single host/port interface; use standalone Daphne's
``--endpoint`` option for arbitrary or multiple endpoints.

HTTP/2 Support
--------------

Daphne supports terminating HTTP/2 connections natively. You'll
need to do a couple of things to get it working, though. First, you need to
make sure you install the Twisted ``http2`` and ``tls`` extras::

    pip install -U "Twisted[tls,http2]"

Daphne negotiates HTTP/2 over TLS using ALPN. HTTPS clients can still use
HTTP/1.1; HTTPS does not require HTTP/2. The plain HTTP listener uses HTTP/1.1
and does not support cleartext HTTP/2 (h2c).

Next, because all current browsers only support HTTP/2 when using TLS, you will
need to start Daphne with TLS turned on, which can be done using the Twisted endpoint syntax::

    daphne -e ssl:443:privateKey=key.pem:certKey=crt.pem django_project.asgi:application

Alternatively, you can use the ``txacme`` endpoint syntax or anything else that
enables TLS under the hood.

For Django's development server, use ``manage.py runserver --certfile`` and
``--keyfile`` as described above. With the Twisted extras installed and an
HTTP/2-capable TLS client, the HTTPS listener negotiates HTTP/2 automatically.
Installing the extras alone does not enable HTTPS on runserver.

You will also need to be on a system that has **OpenSSL 1.0.2 or greater**.

Now, when you start up Daphne, it should tell you this in the log::

    2017-03-18 19:14:02,741 INFO     Starting server at ssl:port=8000:privateKey=privkey.pem:certKey=cert.pem, channel layer django_project.asgi:channel_layer.
    2017-03-18 19:14:02,742 INFO     HTTP/2 support enabled

Then, connect with a browser that supports HTTP/2, and everything should be
working. It's often hard to tell that HTTP/2 is working, as the log Daphne gives you
will be identical (it's HTTP, after all), and most browsers don't make it obvious
in their network inspector windows. There are browser extensions that will let
you know clearly if it's working or not.

Daphne only supports "normal" requests over HTTP/2 at this time; there is not
yet support for extended features like Server Push. It will, however, result in
much faster connections and lower overheads.

If you have a reverse proxy in front of your site to serve static files or
similar, HTTP/2 will only work if that proxy understands and passes through the
connection correctly.


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

    daphne --root-path=/forum django_project.asgi:application


Python Support
--------------

Daphne requires Python 3.10 or later.


Contributing
------------

Please refer to the
`main Channels contributing docs <https://github.com/django/channels/blob/main/CONTRIBUTING.rst>`_.

To run tests, make sure you have installed the ``tests`` extra with the package::

    cd daphne/
    pip install -e '.[tests]'
    pytest


Maintenance and Security
------------------------

To report security issues, please contact security@djangoproject.com. For GPG
signatures and more security process information, see
https://docs.djangoproject.com/en/dev/internals/security/.

To report bugs or request new features, please open a new GitHub issue.

This repository is part of the Channels project. For the shepherd and maintenance team, please see the
`main Channels readme <https://github.com/django/channels/blob/main/README.rst>`_.
