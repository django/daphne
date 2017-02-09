# coding: utf8
from __future__ import unicode_literals

import logging
from unittest import TestCase

from six import string_types

from ..cli import CommandLineInterface
from ..server import Server, build_endpoint_description_strings

# this is the callable that will be tested here
build = build_endpoint_description_strings


class TestEndpointDescriptions(TestCase):
    def testBasics(self):
        self.assertEqual(build(), [], msg="Empty list returned when no kwargs given")

    def testTcpPortBindings(self):
        self.assertEqual(
            build(port=1234, host='example.com'),
            ['tcp:port=1234:interface=example.com']
        )

        self.assertEqual(
            build(port=8000, host='127.0.0.1'),
            ['tcp:port=8000:interface=127.0.0.1']
        )

        self.assertEqual(
            build(port=8000, host='[200a::1]'),
            ['tcp:port=8000:interface=200a\:\:1']
        )

        self.assertEqual(
            build(port=8000, host='200a::1'),
            ['tcp:port=8000:interface=200a\:\:1']
        )

        # incomplete port/host kwargs raise errors
        self.assertRaises(
            ValueError,
            build, port=123
        )
        self.assertRaises(
            ValueError,
            build, host='example.com'
        )

    def testUnixSocketBinding(self):
        self.assertEqual(
            build(unix_socket='/tmp/daphne.sock'),
            ['unix:/tmp/daphne.sock']
        )

    def testFileDescriptorBinding(self):
        self.assertEqual(
            build(file_descriptor=5),
            ['fd:fileno=5']
        )

    def testMultipleEnpoints(self):
        self.assertEqual(
            sorted(
                build(
                    file_descriptor=123,
                    unix_socket='/tmp/daphne.sock',
                    port=8080,
                    host='10.0.0.1'
                )
            ),
            sorted([
                'tcp:port=8080:interface=10.0.0.1',
                'unix:/tmp/daphne.sock',
                'fd:fileno=123'
            ])
        )


class TestCLIInterface(TestCase):
    # construct a string that will be accepted as the channel_layer argument
    _import_channel_layer_string = 'daphne.tests.asgi:channel_layer'

    def setUp(self):
        logging.disable(logging.CRITICAL)
        # patch out the servers run method
        self._default_server_run = Server.run
        Server.run = lambda x: x

    def tearDown(self):
        logging.disable(logging.NOTSET)
        # restore the original server run method
        Server.run = self._default_server_run

    def build_cli(self, cli_args=''):
        # split the string and append the channel_layer positional argument
        if isinstance(cli_args, string_types):
            cli_args = cli_args.split()

        args = cli_args + [self._import_channel_layer_string]
        cli = CommandLineInterface()
        cli.run(args)
        return cli

    def get_endpoints(self, cli_args=''):
        cli = self.build_cli(cli_args=cli_args)
        return cli.server.endpoints

    def checkCLI(self, args='', endpoints=None, msg='Expected endpoints do not match.'):
        endpoints = endpoints or []
        cli = self.build_cli(cli_args=args)
        generated_endpoints = sorted(cli.server.endpoints)
        endpoints.sort()
        self.assertEqual(
            generated_endpoints,
            endpoints,
            msg=msg
        )

    def testCLIBasics(self):
        self.checkCLI(
            '',
            ['tcp:port=8000:interface=127.0.0.1']
        )

        self.checkCLI(
            '-p 123',
            ['tcp:port=123:interface=127.0.0.1']
        )

        self.checkCLI(
            '-b 10.0.0.1',
            ['tcp:port=8000:interface=10.0.0.1']
        )
        self.checkCLI(
            '-b 200a::1',
            ['tcp:port=8000:interface=200a\:\:1']
        )
        self.checkCLI(
            '-b [200a::1]',
            ['tcp:port=8000:interface=200a\:\:1']
        )
        self.checkCLI(
            '-p 8080 -b example.com',
            ['tcp:port=8080:interface=example.com']
        )

    def testCLIEndpointCreation(self):
        self.checkCLI(
            '-p 8080 -u /tmp/daphne.sock',
            [
                'tcp:port=8080:interface=127.0.0.1',
                'unix:/tmp/daphne.sock',
            ],
            'Default binding host patched in when only port given'
        )

        self.checkCLI(
            '-b example.com -u /tmp/daphne.sock',
            [
                'tcp:port=8000:interface=example.com',
                'unix:/tmp/daphne.sock',
            ],
            'Default port patched in when missing.'
        )

        self.checkCLI(
            '-u /tmp/daphne.sock --fd 5',
            [
                'fd:fileno=5',
                'unix:/tmp/daphne.sock'
            ],
            'File descriptor and unix socket bound, TCP ignored.'
        )

    def testMixedCLIEndpointCreation(self):
        self.checkCLI(
            '-p 8080 -e unix:/tmp/daphne.sock',
            [
                'tcp:port=8080:interface=127.0.0.1',
                'unix:/tmp/daphne.sock'
            ],
            'Mix host/port args with endpoint args'
        )

        self.checkCLI(
            '-p 8080 -e tcp:port=8080:interface=127.0.0.1',
            [
                'tcp:port=8080:interface=127.0.0.1',
            ] * 2,
            'Do not try to de-duplicate endpoint description strings.'
            'This would fail when running the server.'
        )

    def testCustomEndpoints(self):
        self.checkCLI(
            '-e imap:',
            ['imap:']
        )
