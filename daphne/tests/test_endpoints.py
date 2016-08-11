# coding: utf8
from __future__ import unicode_literals
from unittest import TestCase
from six import string_types
from ..server import Server
from ..cli import CommandLineInterface


# this is the callable that will be tested here
build = Server.build_endpoint_description_strings

# patch out the servers run function
Server.run = lambda x: x


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
            ['fd:domain=INET:fileno=5']
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
                'fd:domain=INET:fileno=123'
            ])
        )


class TestCLIInterface(TestCase):

    # construct a string that will be accepted as the channel_layer argument
    _import_channel_layer_string = '.'.join(
        __loader__.name.split('.')[:-1] +
        ['asgi:channel_layer']
    )

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

    def checkCLI(self, args='', endpoints=[], msg='Expected endpoints do not match.'):
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
            '-p 8080 -b example.com',
            ['tcp:port=8080:interface=example.com']
        )

    def testMixedCLIEndpoints(self):
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
                'fd:domain=INET:fileno=5',
                'unix:/tmp/daphne.sock'
            ],
            'File descriptor and unix socket bound, TCP ignored.'
        )

    def testCustomEndpoints(self):
        self.checkCLI(
            '-e imap:',
            ['imap:']
        )






