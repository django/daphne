# coding: utf8

from unittest import TestCase

from twisted.web.http_headers import Headers

from daphne.utils import parse_x_forwarded_for


class TestXForwardedForHttpParsing(TestCase):
    """
    Tests that the parse_x_forwarded_for util correctly parses twisted Header.
    """

    def test_basic(self):
        headers = Headers(
            {
                b"X-Forwarded-For": [b"10.1.2.3"],
                b"X-Forwarded-Port": [b"1234"],
                b"X-Forwarded-Proto": [b"https"],
            }
        )
        result = parse_x_forwarded_for(headers)
        self.assertEqual(result, (["10.1.2.3", 1234], "https"))
        self.assertIsInstance(result[0][0], str)
        self.assertIsInstance(result[1], str)

    def test_address_only(self):
        headers = Headers({b"X-Forwarded-For": [b"10.1.2.3"]})
        self.assertEqual(parse_x_forwarded_for(headers), (["10.1.2.3", 0], None))

    def test_v6_address(self):
        headers = Headers({b"X-Forwarded-For": [b"1043::a321:0001, 10.0.5.6"]})
        self.assertEqual(parse_x_forwarded_for(headers), (["1043::a321:0001", 0], None))

    def test_multiple_proxys(self):
        headers = Headers({b"X-Forwarded-For": [b"10.1.2.3, 10.1.2.4"]})
        self.assertEqual(parse_x_forwarded_for(headers), (["10.1.2.3", 0], None))

    def test_original(self):
        headers = Headers({})
        self.assertEqual(
            parse_x_forwarded_for(headers, original_addr=["127.0.0.1", 80]),
            (["127.0.0.1", 80], None),
        )

    def test_no_original(self):
        headers = Headers({})
        self.assertEqual(parse_x_forwarded_for(headers), (None, None))


class TestXForwardedForWsParsing(TestCase):
    """
    Tests that the parse_x_forwarded_for util correctly parses dict headers.
    """

    def test_basic(self):
        headers = {
            b"X-Forwarded-For": b"10.1.2.3",
            b"X-Forwarded-Port": b"1234",
            b"X-Forwarded-Proto": b"https",
        }
        self.assertEqual(parse_x_forwarded_for(headers), (["10.1.2.3", 1234], "https"))

    def test_address_only(self):
        headers = {b"X-Forwarded-For": b"10.1.2.3"}
        self.assertEqual(parse_x_forwarded_for(headers), (["10.1.2.3", 0], None))

    def test_v6_address(self):
        headers = {b"X-Forwarded-For": [b"1043::a321:0001, 10.0.5.6"]}
        self.assertEqual(parse_x_forwarded_for(headers), (["1043::a321:0001", 0], None))

    def test_multiple_proxies(self):
        headers = {b"X-Forwarded-For": b"10.1.2.3, 10.1.2.4"}
        self.assertEqual(parse_x_forwarded_for(headers), (["10.1.2.3", 0], None))

    def test_original(self):
        headers = {}
        self.assertEqual(
            parse_x_forwarded_for(headers, original_addr=["127.0.0.1", 80]),
            (["127.0.0.1", 80], None),
        )

    def test_no_original(self):
        headers = {}
        self.assertEqual(parse_x_forwarded_for(headers), (None, None))
