# coding: utf8

from unittest import TestCase

from twisted.web.http_headers import Headers

from daphne.utils import parse_x_forwarded_for


class TestXForwardedForHttpParsing(TestCase):
    """
    Tests that the parse_x_forwarded_for util correctly parses twisted Header.
    """

    def test_basic(self):
        headers = Headers({
            b"X-Forwarded-For": [b"10.1.2.3"],
            b"X-Forwarded-Port": [b"1234"]
        })
        result = parse_x_forwarded_for(headers)
        self.assertEqual(result, ["10.1.2.3", 1234])
        self.assertIsInstance(result[0], str)

    def test_address_only(self):
        headers = Headers({
            b"X-Forwarded-For": [b"10.1.2.3"],
        })
        self.assertEqual(
            parse_x_forwarded_for(headers),
            ["10.1.2.3", 0]
        )

    def test_v6_address_1(self):
        headers = Headers({
            b"X-Forwarded-For": [b"1043::a321:0001, 10.0.5.6"],
        })
        self.assertEqual(
            parse_x_forwarded_for(headers),
            ["1043::a321:0001", 0]
        )

    def test_multiple_proxys(self):
        headers = Headers({
            b"X-Forwarded-For": [b"10.1.2.3, 10.1.2.4"],
        })
        self.assertEqual(
            parse_x_forwarded_for(headers),
            ["10.1.2.3", 0]
        )

    def test_original(self):
        headers = Headers({})
        self.assertEqual(
            parse_x_forwarded_for(headers, original=["127.0.0.1", 80]),
            ["127.0.0.1", 80]
        )

    def test_no_original(self):
        headers = Headers({})
        self.assertIsNone(parse_x_forwarded_for(headers))


class TestXForwardedForWsParsing(TestCase):
    """
    Tests that the parse_x_forwarded_for util correctly parses dict headers.
    """

    def test_basic(self):
        headers = {
            b"X-Forwarded-For": b"10.1.2.3",
            b"X-Forwarded-Port": b"1234",
        }
        self.assertEqual(
            parse_x_forwarded_for(headers),
            ["10.1.2.3", 1234]
        )

    def test_address_only(self):
        headers = {
            b"X-Forwarded-For": b"10.1.2.3",
        }
        self.assertEqual(
            parse_x_forwarded_for(headers),
            ["10.1.2.3", 0]
        )

    def test_non_bytes_header(self):
        """The passed headers can be non-bytes too."""
        headers = {
            "X-Forwarded-For": "10.1.2.3",
        }
        self.assertEqual(
            parse_x_forwarded_for(headers),
            ["10.1.2.3", 0]
        )

    def test_v6_address_2(self):
        headers = {
            b"X-Forwarded-For": [b"1043::a321:0001, 10.0.5.6"],
        }
        self.assertEqual(
            parse_x_forwarded_for(headers),
            ["1043::a321:0001", 0]
        )

    def test_multiple_proxies(self):
        headers = {
            b"X-Forwarded-For": b"10.1.2.3, 10.1.2.4",
        }
        self.assertEqual(
            parse_x_forwarded_for(headers),
            ["10.1.2.3", 0]
        )

    def test_original(self):
        headers = {}
        self.assertEqual(
            parse_x_forwarded_for(headers, original=["127.0.0.1", 80]),
            ["127.0.0.1", 80]
        )

    def test_no_original(self):
        headers = {}
        self.assertIsNone(parse_x_forwarded_for(headers))
