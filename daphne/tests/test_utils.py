# coding: utf8
from __future__ import unicode_literals
from unittest import TestCase

from twisted.web.http_headers import Headers

from ..utils import parse_x_forwarded_for


class TestXForwardedForParsing(TestCase):
    """
    Tests that the parse_x_forwarded_for util correcly parses headers.
    """

    def test_basic(self):
        headers = Headers({
            b'X-Forwarded-For': [b'10.1.2.3'],
            b'X-Forwarded-Port': [b'1234']
        })
        self.assertEqual(
            parse_x_forwarded_for(headers),
            ['10.1.2.3', 1234]
        )

    def test_address_only(self):
        headers = Headers({
            b'X-Forwarded-For': [b'10.1.2.3'],
        })
        self.assertEqual(
            parse_x_forwarded_for(headers),
            ['10.1.2.3', 0]
        )

    def test_port_in_address(self):
        headers = Headers({
            b'X-Forwarded-For': [b'10.1.2.3:5123'],
        })
        self.assertEqual(
            parse_x_forwarded_for(headers),
            ['10.1.2.3', 5123]
        )

    def test_multiple_proxys(self):
        headers = Headers({
            b'X-Forwarded-For': [b'10.1.2.3, 10.1.2.4'],
        })
        self.assertEqual(
            parse_x_forwarded_for(headers),
            ['10.1.2.4', 0]
        )

    def test_original(self):
        headers = Headers({})
        self.assertEqual(
            parse_x_forwarded_for(headers, original=['127.0.0.1', 80]),
            ['127.0.0.1', 80]
        )

    def test_no_original(self):
        headers = Headers({})
        self.assertIsNone(parse_x_forwarded_for(headers))
