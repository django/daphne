# -*- coding: utf-8 -*-
import functools

from twisted.internet.defer import Deferred, inlineCallbacks
from twisted.internet.protocol import Protocol, Factory
from twisted.internet import endpoints

from h2.connection import H2Connection
from h2.events import (
    RequestReceived, DataReceived, WindowUpdated
)
import time


def close_file(file, d):
    file.close()


READ_CHUNK_SIZE = 8192

class H2Request(object):
    def __init__(self, protocol, id, reply_channel, body_channel=None) :
        self.protocol = protocol
        self.stream_id = id
        self.start_time = time.time()
        self.reply_channel = reply_channel
        self.body_channel = body_channel

    def serverResponse(self, message ):
        print(message)
        self.protocol.makeResponse(self.stream_id, message)
        del self.protocol.factory.reply_protocols[self.reply_channel]

    def DataReceived(self, data) :
        """ chunk of body """
        self.protocol.factory.channel_layer.send(self.body_channel, {
            content: data,
            closed: False,  # send a True to signal interruption of requests
            more_content: False,
        })

    def duration(self):
        return time.time() - self.start_time

    def basic_error(self):
        pass

class H2Protocol(Protocol):
    def __init__(self, factory):
        self.conn = H2Connection(client_side=False)
        self.factory = factory
        self.known_proto = None
        #self.root = root
        self.requests = {} # ongoing requests
        self._flow_control_deferreds = {}

    def connectionMade(self):
        self.conn.initiate_connection()
        self.transport.write(self.conn.data_to_send())

    def dataReceived(self, data):
        if not self.known_proto:
            self.known_proto = True

        events = self.conn.receive_data(data)
        if self.conn.data_to_send:
            self.transport.write(self.conn.data_to_send())

        for event in events:
            if isinstance(event, RequestReceived):
                self.requestReceived(event.headers, event.stream_id)
            elif isinstance(event, DataReceived):
                self.dataFrameReceived(event.stream_id, event.data)
            #elif isinstance(event, WindowUpdated):
            #    self.windowUpdated(event)

    def makeResponse(self, stream_id, message) :

        response_headers = [
            (':status', str(message["status"])),
            ('server', 'twisted-h2'),
            ("status_text", message.get("status_text", "")),
        ]
        for header, value in message.get("headers", []) :
            response_headers.append((header, value))

        self.conn.send_headers(stream_id, response_headers)
        self.transport.write(self.conn.data_to_send())

        # write content .. Chnk this !!
        self.conn.send_data(stream_id, message["content"], True)
        self.transport.write(self.conn.data_to_send())



    def requestReceived(self, headers, stream_id):
        headers = dict(headers)  # Invalid conversion, fix later.

        reply_channel = self.factory.channel_layer.new_channel("http.response!")

        # how do we know if there's a pending body ??
        # body_channel = self.factory.channel_layer.new_channel("http.request.body!")
        req = H2Request(self, stream_id, reply_channel, None)

        self.requests[stream_id] = req
        self.factory.reply_protocols[reply_channel] = req

        path = headers[':path']
        query_string = b""
        if "?" in path: # h2 makes path a unicode
            path, query_string = path.encode().split(b"?", 1)

        self.factory.channel_layer.send("http.request", {
            "reply_channel":  reply_channel,
            "http_version": "2.0", # \o/
            "scheme": "http", # should be read from env/proxys headers ??
            "method" : headers[':method'],
            "path" : path, # asgi expects these as bytes
            "query_string" : query_string,
            "headers": headers,
            "body": b"",  # this is populated on DataReceived event
            "client": [self.transport.getHost().host, self.transport.getHost().port],
        })



    def dataFrameReceived(self, stream_id, data):
        self.requests[stream_id].dataReceived(data)




class H2Factory(Factory):

    def __init__(self, channel_layer, action_logger=None, timeout=120, websocket_timeout=86400, ping_interval=20):
        self.channel_layer = channel_layer
        self.action_logger = action_logger
        self.timeout = timeout
        self.websocket_timeout = websocket_timeout
        self.ping_interval = ping_interval
        # We track all sub-protocols for response channel mapping
        self.reply_protocols = {}
        # Make a factory for WebSocket protocols
        # self.ws_factory = WebSocketFactory(self)
        # self.ws_factory.protocol = WebSocketProtocol
        # self.ws_factory.reply_protocols = self.reply_protocols

    def buildProtocol(self, addr):
        return H2Protocol(self)


    # copy pasta from http_protocol
    def dispatch_reply(self, channel, message):
        if channel.startswith("http") and isinstance(self.reply_protocols[channel], H2Request):
            self.reply_protocols[channel].serverResponse(message)
        # elif channel.startswith("websocket") and isinstance(self.reply_protocols[channel], WebSocketProtocol):
        #     if message.get("bytes", None):
        #         self.reply_protocols[channel].serverSend(message["bytes"], True)
        #     if message.get("text", None):
        #         self.reply_protocols[channel].serverSend(message["text"], False)
        #     if message.get("close", False):
        #         self.reply_protocols[channel].serverClose()
        else:
            raise ValueError("Cannot dispatch message on channel %r" % channel)

    # copy pasta from http protocol
    def reply_channels(self):
        return self.reply_protocols.keys()


    def log_action(self, protocol, action, details):
        """
        Dispatches to any registered action logger, if there is one.
        """
        if self.action_logger:
            self.action_logger(protocol, action, details)

    def check_timeouts(self):
        """
        Runs through all HTTP protocol instances and times them out if they've
        taken too long (and so their message is probably expired)
        """
        for protocol in list(self.reply_protocols.values()):
            # Web timeout checking
            if isinstance(protocol, H2Request) and protocol.duration() > self.timeout:
                protocol.basic_error(503, b"Service Unavailable", "Worker server failed to respond within time limit.")
            # WebSocket timeout checking and keepalive ping sending
            #elif isinstance(protocol, WebSocketProtocol):
                # Timeout check
            #    if protocol.duration() > self.websocket_timeout:
            #        protocol.serverClose()
                # Ping check
            #    else:
            #        protocol.check_ping()
