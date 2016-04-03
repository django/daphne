from unittest import TestCase
from asgiref.inmemory import ChannelLayer
from twisted.test import proto_helpers

from ..http2_protocol import H2Factory
from h2.connection import H2Connection
import h2.events

class TestH2Protocol(TestCase):
    """
    Tests that the HTTP protocol class correctly generates and parses messages.
    """

    def setUp(self):
        self.channel_layer = ChannelLayer()
        self.factory = H2Factory(self.channel_layer)
        self.proto = self.factory.buildProtocol(('127.0.0.1', 0))
        self.tr = proto_helpers.StringTransport()
        self.proto.makeConnection(self.tr)


    def assertStartsWith(self, data, prefix):
        real_prefix = data[:len(prefix)]
        self.assertEqual(real_prefix, prefix)

    def test_basic(self):
        """
        Tests basic HTTP parsing
        """
        # Send a simple request to the protocol

        conn = H2Connection()
        conn.initiate_connection()
        #self.tr.write(conn.data_to_send())
        self.proto.dataReceived(conn.data_to_send())
        conn.send_headers(1, [
            (':method', 'GET'),
            (':path', '/test/?foo=bar'),
            ('user-agent', 'hyper-h2/yo'),
        ], end_stream=True)
        self.proto.dataReceived(conn.data_to_send())

        _, message = self.channel_layer.receive_many(["http.request"])
        self.assertEqual(message['http_version'], "2.0")
        self.assertEqual(message['method'], "GET")
        self.assertEqual(message['scheme'], "http")
        self.assertEqual(message['path'], b"/test/")
        self.assertEqual(message['query_string'], b"foo=bar")
        # self.assertEqual(message['headers'], [(b"user-agent", b"hyper-h2/yo")])
        self.assertFalse(message.get("body", None))
        self.assertTrue(message['reply_channel'])

        # Send back an example response
        self.factory.dispatch_reply(
            message['reply_channel'],
            {
                "status": 201,
                "status_text": b"Created",
                "content": b"OH HAI",
                "headers": [[b"X-Test", b"Boom!"]],
            }
        )
        # Make sure that comes back right on the protocol
        data = self.tr.value()
        evs = conn.receive_data(data)
        # we should see a ResponseReceived
        hasResponse = False
        for e  in evs :
            if isinstance(e, h2.events.ResponseReceived) :
                hasResponse = True
                headers = dict(e.headers)
                self.assertEqual(headers["x-test"],"Boom!")
                self.assertEqual(headers[":status"], "201")

            if isinstance(e, h2.events.DataReceived):
                self.assertEqual(e.data, b"OH HAI")
        self.assertTrue(hasResponse)

        # a DataReceived
        # a StreamEnded ??

        # self.assertEqual(evs)
        # self.assertEqual(self.tr.value(), b"HTTP/1.1 201 Created\r\nTransfer-Encoding: chunked\r\nX-Test: Boom!\r\n\r\n6\r\nOH HAI\r\n0\r\n\r\n")
