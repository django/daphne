from __future__ import unicode_literals

import json

from django.test import override_settings
from django.contrib.auth import get_user_model

from channels import route_class
from channels.exceptions import SendNotAvailableOnDemultiplexer
from channels.generic import BaseConsumer, websockets
from channels.test import ChannelTestCase, Client, WSClient, apply_routes


@override_settings(SESSION_ENGINE="django.contrib.sessions.backends.cache")
class GenericTests(ChannelTestCase):

    def test_base_consumer(self):

        class Consumers(BaseConsumer):

            method_mapping = {
                'test.create': 'create',
                'test.test': 'test',
            }

            def create(self, message, **kwargs):
                self.called = 'create'

            def test(self, message, **kwargs):
                self.called = 'test'

        with apply_routes([route_class(Consumers)]):
            client = Client()

            #  check that methods for certain channels routes successfully
            self.assertEqual(client.send_and_consume('test.create').called, 'create')
            self.assertEqual(client.send_and_consume('test.test').called, 'test')

            #  send to the channels without routes
            client.send('test.wrong')
            message = self.get_next_message('test.wrong')
            self.assertEqual(client.channel_layer.router.match(message), None)

            client.send('test')
            message = self.get_next_message('test')
            self.assertEqual(client.channel_layer.router.match(message), None)

    def test_websockets_consumers_handlers(self):

        class WebsocketConsumer(websockets.WebsocketConsumer):

            def connect(self, message, **kwargs):
                self.called = 'connect'
                self.id = kwargs['id']

            def disconnect(self, message, **kwargs):
                self.called = 'disconnect'

            def receive(self, text=None, bytes=None, **kwargs):
                self.text = text

        with apply_routes([route_class(WebsocketConsumer, path='/path/(?P<id>\d+)')]):
            client = Client()

            consumer = client.send_and_consume('websocket.connect', {'path': '/path/1'})
            self.assertEqual(consumer.called, 'connect')
            self.assertEqual(consumer.id, '1')

            consumer = client.send_and_consume('websocket.receive', {'path': '/path/1', 'text': 'text'})
            self.assertEqual(consumer.text, 'text')

            consumer = client.send_and_consume('websocket.disconnect', {'path': '/path/1'})
            self.assertEqual(consumer.called, 'disconnect')

    def test_websockets_decorators(self):
        class WebsocketConsumer(websockets.WebsocketConsumer):
            strict_ordering = True

            def connect(self, message, **kwargs):
                self.order = message['order']

        with apply_routes([route_class(WebsocketConsumer, path='/path')]):
            client = Client()

            client.send('websocket.connect', {'path': '/path', 'order': 1})
            client.send('websocket.connect', {'path': '/path', 'order': 0})
            client.consume('websocket.connect')
            self.assertEqual(client.consume('websocket.connect').order, 0)
            self.assertEqual(client.consume('websocket.connect').order, 1)

    def test_websockets_http_session_and_channel_session(self):

        class WebsocketConsumer(websockets.WebsocketConsumer):
            http_user_and_session = True

        user_model = get_user_model()
        user = user_model.objects.create_user(username='test', email='test@test.com', password='123456')

        client = WSClient()
        client.force_login(user)
        with apply_routes([route_class(WebsocketConsumer, path='/path')]):
            connect = client.send_and_consume('websocket.connect', {'path': '/path'})
            receive = client.send_and_consume('websocket.receive', {'path': '/path'}, text={'key': 'value'})
            disconnect = client.send_and_consume('websocket.disconnect', {'path': '/path'})
        self.assertEqual(
            connect.message.http_session.session_key,
            receive.message.http_session.session_key
        )
        self.assertEqual(
            connect.message.http_session.session_key,
            disconnect.message.http_session.session_key
        )

    def test_simple_as_route_method(self):

        class WebsocketConsumer(websockets.WebsocketConsumer):

            def connect(self, message, **kwargs):
                self.message.reply_channel.send({'accept': True})
                self.send(text=message.get('order'))

        routes = [
            WebsocketConsumer.as_route(attrs={"strict_ordering": True}, path='^/path$'),
            WebsocketConsumer.as_route(path='^/path/2$'),
        ]

        self.assertIsNot(routes[0].consumer, WebsocketConsumer)
        self.assertIs(routes[1].consumer, WebsocketConsumer)

        with apply_routes(routes):
            client = WSClient()

            client.send('websocket.connect', {'path': '/path', 'order': 1})
            client.send('websocket.connect', {'path': '/path', 'order': 0})
            client.consume('websocket.connect', check_accept=False)
            client.consume('websocket.connect')
            self.assertEqual(client.receive(json=False), 0)
            client.consume('websocket.connect')
            self.assertEqual(client.receive(json=False), 1)

            client.send_and_consume('websocket.connect', {'path': '/path/2', 'order': 'next'})
            self.assertEqual(client.receive(json=False), 'next')

    def test_as_route_method(self):
        class WebsocketConsumer(BaseConsumer):
            trigger = 'new'

            def test(self, message, **kwargs):
                self.message.reply_channel.send({'trigger': self.trigger})

        method_mapping = {'mychannel': 'test'}

        with apply_routes([
            WebsocketConsumer.as_route(
                {'method_mapping': method_mapping, 'trigger': 'from_as_route'},
                name='filter',
            ),
        ]):
            client = Client()

            client.send_and_consume('mychannel', {'name': 'filter'})
            self.assertEqual(client.receive(), {'trigger': 'from_as_route'})

    def test_websockets_demultiplexer(self):

        class MyWebsocketConsumer(websockets.JsonWebsocketConsumer):
            def connect(self, message, multiplexer=None, **kwargs):
                multiplexer.send(kwargs)

            def disconnect(self, message, multiplexer=None, **kwargs):
                multiplexer.send(kwargs)

            def receive(self, content, multiplexer=None, **kwargs):
                multiplexer.send(content)

        class Demultiplexer(websockets.WebsocketDemultiplexer):

            consumers = {
                "mystream": MyWebsocketConsumer
            }

        with apply_routes([route_class(Demultiplexer, path='/path/(?P<id>\d+)')]):
            client = WSClient()

            client.send_and_consume('websocket.connect', path='/path/1')
            self.assertEqual(client.receive(), {
                "stream": "mystream",
                "payload": {"id": "1"},
            })

            client.send_and_consume('websocket.receive', text={
                "stream": "mystream",
                "payload": {"text_field": "mytext"},
            }, path='/path/1')
            self.assertEqual(client.receive(), {
                "stream": "mystream",
                "payload": {"text_field": "mytext"},
            })

            client.send_and_consume('websocket.disconnect', path='/path/1')
            self.assertEqual(client.receive(), {
                "stream": "mystream",
                "payload": {"id": "1"},
            })

    def test_websocket_demultiplexer_send(self):

        class MyWebsocketConsumer(websockets.JsonWebsocketConsumer):
            def receive(self, content, multiplexer=None, **kwargs):
                self.send(content)

        class Demultiplexer(websockets.WebsocketDemultiplexer):

            consumers = {
                "mystream": MyWebsocketConsumer
            }

        with apply_routes([route_class(Demultiplexer, path='/path/(?P<id>\d+)')]):
            client = WSClient()

            with self.assertRaises(SendNotAvailableOnDemultiplexer):
                client.send_and_consume('websocket.receive', path='/path/1', text={
                    "stream": "mystream",
                    "payload": {"text_field": "mytext"},
                })

                client.receive()

    def test_websocket_custom_json_serialization(self):

        class WebsocketConsumer(websockets.JsonWebsocketConsumer):
            @classmethod
            def decode_json(cls, text):
                obj = json.loads(text)
                return dict((key.upper(), obj[key]) for key in obj)

            @classmethod
            def encode_json(cls, content):
                lowered = dict((key.lower(), content[key]) for key in content)
                return json.dumps(lowered)

            def receive(self, content, multiplexer=None, **kwargs):
                self.content_received = content
                self.send({"RESPONSE": "HI"})

        class MyMultiplexer(websockets.WebsocketMultiplexer):
            @classmethod
            def encode_json(cls, content):
                lowered = dict((key.lower(), content[key]) for key in content)
                return json.dumps(lowered)

        with apply_routes([route_class(WebsocketConsumer, path='/path')]):
            client = WSClient()

            consumer = client.send_and_consume('websocket.receive', path='/path', text={"key": "value"})
            self.assertEqual(consumer.content_received, {"KEY": "value"})

            self.assertEqual(client.receive(), {"response": "HI"})

            client.join_group('test_group')
            WebsocketConsumer.group_send('test_group', {"KEY": "VALUE"})
            self.assertEqual(client.receive(), {"key": "VALUE"})

    def test_websockets_demultiplexer_custom_multiplexer(self):

        class MyWebsocketConsumer(websockets.JsonWebsocketConsumer):
            def connect(self, message, multiplexer=None, **kwargs):
                multiplexer.send({"THIS_SHOULD_BE_LOWERCASED": "1"})

        class MyMultiplexer(websockets.WebsocketMultiplexer):
            @classmethod
            def encode_json(cls, content):
                lowered = {
                    "stream": content["stream"],
                    "payload": dict((key.lower(), content["payload"][key]) for key in content["payload"])
                }
                return json.dumps(lowered)

        class Demultiplexer(websockets.WebsocketDemultiplexer):
            multiplexer_class = MyMultiplexer

            consumers = {
                "mystream": MyWebsocketConsumer
            }

        with apply_routes([route_class(Demultiplexer, path='/path/(?P<ID>\d+)')]):
            client = WSClient()

            client.send_and_consume('websocket.connect', path='/path/1')
            self.assertEqual(client.receive(), {
                "stream": "mystream",
                "payload": {"this_should_be_lowercased": "1"},
            })
