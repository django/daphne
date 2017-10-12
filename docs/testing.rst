Testing Consumers
=================

When you want to write unit tests for your new Channels consumers, you'll
realize that you can't use the standard Django test client to submit fake HTTP
requests - instead, you'll need to submit fake Messages to your consumers,
and inspect what Messages they send themselves.

We provide a ``TestCase`` subclass that sets all of this up for you,
however, so you can easily write tests and check what your consumers are sending.


ChannelTestCase
---------------

If your tests inherit from the ``channels.test.ChannelTestCase`` base class,
whenever you run tests your channel layer will be swapped out for a captive
in-memory layer, meaning you don't need an external server running to run tests.

Moreover, you can inject messages onto this layer and inspect ones sent to it
to help test your consumers.

To inject a message onto the layer, simply call ``Channel.send()`` inside
any test method on a ``ChannelTestCase`` subclass, like so::

    from channels import Channel
    from channels.test import ChannelTestCase

    class MyTests(ChannelTestCase):
        def test_a_thing(self):
            # This goes onto an in-memory channel, not the real backend.
            Channel("some-channel-name").send({"foo": "bar"})

To receive a message from the layer, you can use ``self.get_next_message(channel)``,
which handles receiving the message and converting it into a Message object for
you (if you want, you can call ``receive_many`` on the underlying channel layer,
but you'll get back a raw dict and channel name, which is not what consumers want).

You can use this both to get Messages to send to consumers as their primary
argument, as well as to get Messages from channels that consumers are supposed
to send on to verify that they did.

You can even pass ``require=True`` to ``get_next_message`` to make the test
fail if there is no message on the channel (by default, it will return you
``None`` instead).

Here's an extended example testing a consumer that's supposed to take a value
and post the square of it to the ``"result"`` channel::


    from channels import Channel
    from channels.test import ChannelTestCase

    class MyTests(ChannelTestCase):
        def test_a_thing(self):
            # Inject a message onto the channel to use in a consumer
            Channel("input").send({"value": 33})
            # Run the consumer with the new Message object
            my_consumer(self.get_next_message("input", require=True))
            # Verify there's a result and that it's accurate
            result = self.get_next_message("result", require=True)
            self.assertEqual(result['value'], 1089)


Generic Consumers
-----------------

You can use ``ChannelTestCase`` to test generic consumers as well. Just pass the message
object from ``get_next_message`` to the constructor of the class. To test replies to a specific channel,
use the ``reply_channel`` property on the ``Message`` object. For example::

    from channels import Channel
    from channels.test import ChannelTestCase

    from myapp.consumers import MyConsumer

    class MyTests(ChannelTestCase):

        def test_a_thing(self):
            # Inject a message onto the channel to use in a consumer
            Channel("input").send({"value": 33})
            # Run the consumer with the new Message object
            message = self.get_next_message("input", require=True)
            MyConsumer(message)
            # Verify there's a reply and that it's accurate
            result = self.get_next_message(message.reply_channel.name, require=True)
            self.assertEqual(result['value'], 1089)


Groups
------

You can test Groups in the same way as Channels inside a ``ChannelTestCase``;
the entire channel layer is flushed each time a test is run, so it's safe to
do group adds and sends during a test. For example::

    from channels import Group
    from channels.test import ChannelTestCase

    class MyTests(ChannelTestCase):
        def test_a_thing(self):
            # Add a test channel to a test group
            Group("test-group").add("test-channel")
            # Send to the group
            Group("test-group").send({"value": 42})
            # Verify the message got into the destination channel
            result = self.get_next_message("test-channel", require=True)
            self.assertEqual(result['value'], 42)


Clients
-------

For more complicated test suites you can use the ``Client`` abstraction that
provides an easy way to test the full life cycle of messages with a couple of methods:
``send`` to sending message with given content to the given channel, ``consume``
to run appointed consumer for the next message, ``receive`` to getting replies for client.
Very often you may need to ``send`` and than call a consumer one by one, for this
purpose use ``send_and_consume`` method::

    from channels.test import ChannelTestCase, Client

    class MyTests(ChannelTestCase):

        def test_my_consumer(self):
            client = Client()
            client.send_and_consume('my_internal_channel', {'value': 'my_value'})
            self.assertEqual(client.receive(), {'all is': 'done'})
            
*Note: if testing consumers that are expected to close the connection when consuming, set the ``check_accept`` parameter to False on ``send_and_consume``.*

You can use ``WSClient`` for websocket related consumers. It automatically serializes JSON content,
manage cookies and headers, give easy access to the session and add ability to authorize your requests.
For example::


    # consumers.py
    class RoomConsumer(JsonWebsocketConsumer):
        http_user = True
        groups = ['rooms_watchers']

        def receive(self, content, **kwargs):
            self.send({'rooms': self.message.http_session.get("rooms", [])})
            Channel("rooms_receive").send({'user': self.message.user.id,
                                           'message': content['message']}


    # tests.py
    from channels import Group
    from channels.test import ChannelTestCase, WSClient


    class RoomsTests(ChannelTestCase):

        def test_rooms(self):
            client = WSClient()
            user = User.objects.create_user(
                username='test', email='test@test.com', password='123456')
            client.login(username='test', password='123456')

            client.send_and_consume('websocket.connect', path='/rooms/')
            # check that there is nothing to receive
            self.assertIsNone(client.receive())

            # test that the client in the group
            Group(RoomConsumer.groups[0]).send({'text': 'ok'}, immediately=True)
            self.assertEqual(client.receive(json=False), 'ok')

            client.session['rooms'] = ['test', '1']
            client.session.save()

            client.send_and_consume('websocket.receive',
                                    text={'message': 'hey'},
                                    path='/rooms/')
            # test 'response'
            self.assertEqual(client.receive(), {'rooms': ['test', '1']})

            self.assertEqual(self.get_next_message('rooms_receive').content,
                             {'user': user.id, 'message': 'hey'})

            # There is nothing to receive
            self.assertIsNone(client.receive())


Instead of ``WSClient.login`` method with credentials at arguments you
may call ``WSClient.force_login`` (like at django client) with the user object.

``receive`` method by default trying to deserialize json text content of a message,
so if you need to pass decoding use ``receive(json=False)``, like in the example.

For testing consumers with ``enforce_ordering`` initialize ``HttpClient`` with ``ordered``
flag, but if you wanna use your own order don't use it, use content::

    client = HttpClient(ordered=True)
    client.send_and_consume('websocket.receive', text='1', path='/ws')  # order = 0
    client.send_and_consume('websocket.receive', text='2', path='/ws')  # order = 1
    client.send_and_consume('websocket.receive', text='3', path='/ws')  # order = 2

    # manually
    client = HttpClient()
    client.send('websocket.receive', content={'order': 0}, text='1')
    client.send('websocket.receive', content={'order': 2}, text='2')
    client.send('websocket.receive', content={'order': 1}, text='3')

    # calling consume 4 time for `waiting` message with order 1
    client.consume('websocket.receive')
    client.consume('websocket.receive')
    client.consume('websocket.receive')
    client.consume('websocket.receive')


Applying routes
---------------

When you need to test your consumers without routes in settings or you
want to test your consumers in a more isolate and atomic way, it will be
simpler with ``apply_routes`` contextmanager and decorator for your ``ChannelTestCase``.
It takes a list of routes that you want to use and overwrites existing routes::

    from channels.test import ChannelTestCase, WSClient, apply_routes

    class MyTests(ChannelTestCase):

        def test_myconsumer(self):
            client = WSClient()

            with apply_routes([MyConsumer.as_route(path='/new')]):
                client.send_and_consume('websocket.connect', '/new')
                self.assertEqual(client.receive(), {'key': 'value'})


Test Data binding with ``WSClient``
-------------------------------------

As you know data binding in channels works in outbound and inbound ways,
so that ways tests in different ways and ``WSClient`` and ``apply_routes``
will help to do this.
When you testing outbound consumers you need just import your ``Binding``
subclass with specified ``group_names``. At test you can  join to one of them,
make some changes with target model and check received message.
Lets test ``IntegerValueBinding`` from :doc:`data binding <binding>`
with creating::

    from channels.test import ChannelTestCase, WSClient
    from channels.signals import consumer_finished

    class TestIntegerValueBinding(ChannelTestCase):

        def test_outbound_create(self):
            # We use WSClient because of json encoding messages
            client = WSClient()
            client.join_group("intval-updates")  # join outbound binding

            # create target entity
            value = IntegerValue.objects.create(name='fifty', value=50)

            received = client.receive()  # receive outbound binding message
            self.assertIsNotNone(received)

            self.assertTrue('payload' in received)
            self.assertTrue('action' in received['payload'])
            self.assertTrue('data' in received['payload'])
            self.assertTrue('name' in received['payload']['data'])
            self.assertTrue('value' in received['payload']['data'])

            self.assertEqual(received['payload']['action'], 'create')
            self.assertEqual(received['payload']['model'], 'values.integervalue')
            self.assertEqual(received['payload']['pk'], value.pk)

            self.assertEqual(received['payload']['data']['name'], 'fifty')
            self.assertEqual(received['payload']['data']['value'], 50)

            # assert that is nothing to receive
            self.assertIsNone(client.receive())


There is another situation with inbound binding. It is used with :ref:`multiplexing`,
So we apply two routes: websocket route for demultiplexer and route with internal
consumer for binding itself, connect to websocket entrypoint and test different actions.
For example::

    class TestIntegerValueBinding(ChannelTestCase):

        def test_inbound_create(self):
            # check that initial state is empty
            self.assertEqual(IntegerValue.objects.all().count(), 0)

            with apply_routes([Demultiplexer.as_route(path='/'),
                              route("binding.intval", IntegerValueBinding.consumer)]):
                client = WSClient()
                client.send_and_consume('websocket.connect', path='/')
                client.send_and_consume('websocket.receive', path='/', text={
                    'stream': 'intval',
                    'payload': {'action': CREATE, 'data': {'name': 'one', 'value': 1}}
                })
                # our Demultiplexer route message to the inbound consumer,
                # so we need to call this consumer
                client.consume('binding.users')

            self.assertEqual(IntegerValue.objects.all().count(), 1)
            value = IntegerValue.objects.all().first()
            self.assertEqual(value.name, 'one')
            self.assertEqual(value.value, 1)



Multiple Channel Layers
-----------------------

If you want to test code that uses multiple channel layers, specify the alias
of the layers you want to mock as the ``test_channel_aliases`` attribute on
the ``ChannelTestCase`` subclass; by default, only the ``default`` layer is
mocked.

You can pass an ``alias`` argument to ``get_next_message``, ``Client`` and ``Channel``
to use a different layer too.

Live Server Test Case
---------------------

You can use browser automation libraries like Selenium or Splinter to
check your application against real layer installation.  First of all
provide ``TEST_CONFIG`` setting to prevent overlapping with running
dev environment.

.. code:: python

    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "asgi_redis.RedisChannelLayer",
            "ROUTING": "my_project.routing.channel_routing",
            "CONFIG": {
                "hosts": [("redis-server-name", 6379)],
            },
            "TEST_CONFIG": {
                "hosts": [("localhost", 6379)],
            },
        },
    }

Now use ``ChannelLiveServerTestCase`` for your acceptance tests.

.. code:: python

    from channels.test import ChannelLiveServerTestCase
    from splinter import Browser

    class IntegrationTest(ChannelLiveServerTestCase):

        def test_browse_site_index(self):

            with Browser() as browser:

                browser.visit(self.live_server_url)
                # the rest of your integration test...

In the test above Daphne and Channels worker processes were fired up.
These processes run your project against the test database and the
default channel layer you spacify in the settings.  If channel layer
support ``flush`` extension, initial cleanup will be done.  So do not
run this code against your production environment. 
ChannelLiveServerTestCase can not be used with in memory databases.
When using the SQLite database engine the Django tests will by default 
use an in-memory database. To disable this add the ``TEST`` setting
to the database configuration.

.. code:: python

    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
            'TEST': {
                'NAME': 'testdb.sqlite3'
            }
        }
    }

When channels
infrastructure is ready default web browser will be also started.  You
can open your website in the real browser which can execute JavaScript
and operate on WebSockets.  ``live_server_ws_url`` property is also
provided if you decide to run messaging directly from Python.

By default live server test case will serve static files.  To disable
this feature override `serve_static` class attribute.

.. code:: python

    class IntegrationTest(ChannelLiveServerTestCase):

        serve_static = False

        def test_websocket_message(self):
            # JS and CSS are not available in this test.
            ...
