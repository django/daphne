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
        self.response_started = False
        self.headers = {}
        self._header_sent = False # have header message been sent to channel layer ?

    def setHeaders(self, headers) :
        self.headers = headers
        self.body = b""

    def sendHeaders(self):

        path = self.headers[':path']
        query_string = b""
        if "?" in path: # h2 makes path a unicode
            path, query_string = path.encode().split(b"?", 1)

        # clean up ':' prefixed headers
        headers_ = {}
        for k,v in self.headers.items() :
            if not k.startswith(':'):
                headers_[k] = v

        # not post : wait for body before sending message
        self.protocol.factory.channel_layer.send("http.request", {
            "reply_channel":  self.reply_channel,
            "http_version": "2.0", # \o/
            "scheme": "http", # should be read from env/proxys headers ??
            "method" : self.headers[':method'],
            "path" : path, # asgi expects these as bytes
            "query_string" : query_string,
            "headers": headers_,
            "body": self.body,  # this is populated on DataReceived event
            "client": [self.protocol.transport.getHost().host,
                       self.protocol.transport.getHost().port],
        })

        self._header_send = True

    def serverResponse(self, message ):
        if "status" in message :
            assert(not self.response_started)
            self.response_started = True
            self.protocol.makeResponse(self.stream_id, message)
            # only if we are done
        else :
            assert(self.response_started)
            self.protocol.sendData(self.stream_id,
                                   message["content"],
                                   message["more_content"])

        if(not message.get("more_content", False)) :
            del self.protocol.factory.reply_protocols[self.reply_channel]



    def dataReceived(self, data) :
        """ chunk of body received """
        if(self._header_sent and self.body_channel) :
            self.protocol.factory.channel_layer.send(self.body_channel, {
                "content": data,
                "closed": False,  # send a True to signal interruption of requests
                "more_content": False, # we just can't know that ..
            })
        else :
            print("Barf!")

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
            elif isinstance(event, WindowUpdated):
                self.windowUpdated(event)

    def makeResponse(self, stream_id, message) :
        print("responding", message)
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
        more_content = message.get('more_content', False)
        # that's a twisted deferred, if you don't add a call back,
        # this gets discarded
        d = self.sendData(stream_id, message["content"], more_content)
        d.addErrback(lambda e: print("error in send data", e))


    def requestReceived(self, headers, stream_id):
        headers = dict(headers)  # Invalid conversion, fix later.

        reply_channel = self.factory.channel_layer.new_channel("http.response!")

        body_channel = None
        if(headers[':method'] == 'POST'):
            body_channel = self.factory.channel_layer.new_channel("http.request.body!")
        # body_channel =
        req = H2Request(self, stream_id, reply_channel, body_channel)
        req.setHeaders(headers)

        self.requests[stream_id] = req
        self.factory.reply_protocols[reply_channel] = req

        # send the request to channel layer, or wait for body
        req.sendHeaders()


    @inlineCallbacks
    def sendData(self, stream_id, data, more_content=False):
        # chunks and enqueue data
        send_more = True
        msg_size = len(data)
        offset = 0
        while send_more :
            print("waigint for flow control")
            while not self.conn.remote_flow_control_window(stream_id) :
                # do we have a flow window ?
                yield self.wait_for_flow_control(stream_id)

            chunk_size = min(self.conn.remote_flow_control_window(stream_id),READ_CHUNK_SIZE)

            # hopefully, both are bigger than message data
            if (msg_size - offset) < chunk_size :
                send_more = False
                end_chunk = offset + chunk_size + 1
            else :
                end_chunk = msg_size + 1

            chunk = data[offset:end_chunk]
            # if more_content, keep request active
            done = not ( send_more or  more_content)
            self.conn.send_data(stream_id, chunk, done)
            self.transport.write(self.conn.data_to_send())


    def wait_for_flow_control(self, stream_id):
        d = Deferred()
        self._flow_control_deferreds[stream_id] = d
        return d

    def dataFrameReceived(self, stream_id, data):
        self.requests[stream_id].dataReceived(data)

    def windowUpdated(self, event):
        stream_id = event.stream_id
        print("window flow ctrl", stream_id)
        if stream_id and stream_id in self._flow_control_deferreds:
            d = self._flow_control_deferreds.pop(stream_id)
            d.callback(event.delta)
        elif not stream_id:
            # fire them all..
            for d in self._flow_control_deferreds.values():
                d.callback(event.delta)
            self._flow_control_deferreds = {}
            return


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
