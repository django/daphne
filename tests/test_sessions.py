from __future__ import unicode_literals

from django.conf import settings
from django.test import override_settings

from channels import DEFAULT_CHANNEL_LAYER, channel_layers
from channels.message import Message
from channels.sessions import (
    channel_and_http_session, channel_session, enforce_ordering, http_session, session_for_reply_channel,
)
from channels.test import ChannelTestCase

try:
    from unittest import mock
except ImportError:
    import mock


@override_settings(SESSION_ENGINE="django.contrib.sessions.backends.cache")
class SessionTests(ChannelTestCase):
    """
    Tests the channels session module.
    """

    def test_session_for_reply_channel(self):
        """
        Tests storing and retrieving values by reply_channel.
        """
        session1 = session_for_reply_channel("test-reply-channel")
        session1["testvalue"] = 42
        session1.save(must_create=True)
        session2 = session_for_reply_channel("test-reply-channel")
        self.assertEqual(session2["testvalue"], 42)

    def test_channel_session(self):
        """
        Tests the channel_session decorator
        """
        # Construct message to send
        message = Message({"reply_channel": "test-reply"}, None, None)

        # Run through a simple fake consumer that assigns to it
        @channel_session
        def inner(message):
            message.channel_session["num_ponies"] = -1

        inner(message)
        # Test the session worked
        session2 = session_for_reply_channel("test-reply")
        self.assertEqual(session2["num_ponies"], -1)

    def test_channel_session_method(self):
        """
        Tests the channel_session decorator works on methods
        """
        # Construct message to send
        message = Message({"reply_channel": "test-reply"}, None, None)

        # Run through a simple fake consumer that assigns to it
        class Consumer(object):
            @channel_session
            def inner(self, message):
                message.channel_session["num_ponies"] = -1

        Consumer().inner(message)
        # Test the session worked
        session2 = session_for_reply_channel("test-reply")
        self.assertEqual(session2["num_ponies"], -1)

    def test_channel_session_third_arg(self):
        """
        Tests the channel_session decorator with message as 3rd argument
        """
        # Construct message to send
        message = Message({"reply_channel": "test-reply"}, None, None)

        # Run through a simple fake consumer that assigns to it
        @channel_session
        def inner(a, b, message):
            message.channel_session["num_ponies"] = -1

        with self.assertRaisesMessage(ValueError, 'channel_session called without Message instance'):
            inner(None, None, message)

    def test_channel_session_double(self):
        """
        Tests the channel_session decorator detects being wrapped in itself
        and doesn't blow up.
        """
        # Construct message to send
        message = Message({"reply_channel": "test-reply"}, None, None)

        # Run through a simple fake consumer that should trigger the error
        @channel_session
        @channel_session
        def inner(message):
            message.channel_session["num_ponies"] = -1
        inner(message)

        # Test the session worked
        session2 = session_for_reply_channel("test-reply")
        self.assertEqual(session2["num_ponies"], -1)

    def test_channel_session_double_method(self):
        """
        Tests the channel_session decorator detects being wrapped in itself
        and doesn't blow up. Method version.
        """
        # Construct message to send
        message = Message({"reply_channel": "test-reply"}, None, None)

        # Run through a simple fake consumer that should trigger the error
        class Consumer(object):
            @channel_session
            @channel_session
            def inner(self, message):
                message.channel_session["num_ponies"] = -1
        Consumer().inner(message)

        # Test the session worked
        session2 = session_for_reply_channel("test-reply")
        self.assertEqual(session2["num_ponies"], -1)

    def test_channel_session_double_third_arg(self):
        """
        Tests the channel_session decorator detects being wrapped in itself
        and doesn't blow up.
        """
        # Construct message to send
        message = Message({"reply_channel": "test-reply"}, None, None)

        # Run through a simple fake consumer that should trigger the error
        @channel_session
        @channel_session
        def inner(a, b, message):
            message.channel_session["num_ponies"] = -1
        with self.assertRaisesMessage(ValueError, 'channel_session called without Message instance'):
            inner(None, None, message)

    def test_channel_session_no_reply(self):
        """
        Tests the channel_session decorator detects no reply channel
        """
        # Construct message to send
        message = Message({}, None, None)

        # Run through a simple fake consumer that should trigger the error
        @channel_session
        @channel_session
        def inner(message):
            message.channel_session["num_ponies"] = -1

        with self.assertRaises(ValueError):
            inner(message)

    def test_channel_session_no_reply_method(self):
        """
        Tests the channel_session decorator detects no reply channel
        """
        # Construct message to send
        message = Message({}, None, None)

        # Run through a simple fake consumer that should trigger the error
        class Consumer(object):
            @channel_session
            @channel_session
            def inner(self, message):
                message.channel_session["num_ponies"] = -1

        with self.assertRaises(ValueError):
            Consumer().inner(message)

    def test_channel_session_no_reply_third_arg(self):
        """
        Tests the channel_session decorator detects no reply channel
        """
        # Construct message to send
        message = Message({}, None, None)

        # Run through a simple fake consumer that should trigger the error
        @channel_session
        @channel_session
        def inner(a, b, message):
            message.channel_session["num_ponies"] = -1

        with self.assertRaisesMessage(ValueError, 'channel_session called without Message instance'):
            inner(None, None, message)

    def test_http_session(self):
        """
        Tests that http_session correctly extracts a session cookie.
        """
        # Make a session to try against
        session1 = session_for_reply_channel("test-reply")
        # Construct message to send
        message = Message({
            "reply_channel": "test-reply",
            "http_version": "1.1",
            "method": "GET",
            "path": "/test2/",
            "headers": {
                "host": b"example.com",
                "cookie": ("%s=%s" % (settings.SESSION_COOKIE_NAME, session1.session_key)).encode("ascii"),
            },
        }, None, None)

        # Run it through http_session, make sure it works (test double here too)
        @http_session
        @http_session
        def inner(message):
            message.http_session["species"] = "horse"

        inner(message)
        # Check value assignment stuck
        session2 = session_for_reply_channel("test-reply")
        self.assertEqual(session2["species"], "horse")

    def test_channel_and_http_session(self):
        """
        Tests that channel_and_http_session decorator stores the http session key and hydrates it when expected
        """
        # Make a session to try against
        session = session_for_reply_channel("test-reply-session")
        # Construct message to send
        message = Message({
            "reply_channel": "test-reply-session",
            "http_version": "1.1",
            "method": "GET",
            "path": "/test2/",
            "headers": {
                "host": b"example.com",
                "cookie": ("%s=%s" % (settings.SESSION_COOKIE_NAME, session.session_key)).encode("ascii"),
            },
        }, None, None)

        @channel_and_http_session
        def inner(message):
            pass

        inner(message)

        # It should store the session key
        self.assertEqual(message.channel_session[settings.SESSION_COOKIE_NAME], session.session_key)

        # Construct a new message
        message2 = Message({"reply_channel": "test-reply-session", "path": "/"}, None, None)

        inner(message2)

        # It should hydrate the http_session
        self.assertEqual(message2.http_session.session_key, session.session_key)

    def test_enforce_ordering(self):
        """
        Tests that strict mode of enforce_ordering works
        """
        # Construct messages to send
        message0 = Message(
            {"reply_channel": "test-reply!b", "order": 0},
            "websocket.connect",
            channel_layers[DEFAULT_CHANNEL_LAYER]
        )
        message1 = Message(
            {"reply_channel": "test-reply!b", "order": 1},
            "websocket.receive",
            channel_layers[DEFAULT_CHANNEL_LAYER]
        )
        message2 = Message(
            {"reply_channel": "test-reply!b", "order": 2},
            "websocket.receive",
            channel_layers[DEFAULT_CHANNEL_LAYER]
        )

        # Run them in an acceptable strict order
        @enforce_ordering
        def inner(message):
            pass

        inner(message0)
        inner(message1)
        inner(message2)

        # Ensure wait channel is empty
        wait_channel = "__wait__.test-reply?b"
        next_message = self.get_next_message(wait_channel)
        self.assertEqual(next_message, None)

    def test_enforce_ordering_fail(self):
        """
        Tests that strict mode of enforce_ordering fails on bad ordering
        """
        # Construct messages to send
        message0 = Message(
            {"reply_channel": "test-reply-c", "order": 0},
            "websocket.connect",
            channel_layers[DEFAULT_CHANNEL_LAYER]
        )
        message2 = Message(
            {"reply_channel": "test-reply-c", "order": 2},
            "websocket.receive",
            channel_layers[DEFAULT_CHANNEL_LAYER]
        )

        # Run them in an acceptable strict order
        @enforce_ordering
        def inner(message):
            pass

        inner(message0)
        inner(message2)

        # Ensure wait channel is not empty
        wait_channel = "__wait__.%s" % "test-reply-c"
        next_message = self.get_next_message(wait_channel)
        self.assertNotEqual(next_message, None)

    def test_enforce_ordering_fail_no_order(self):
        """
        Makes sure messages with no "order" key fail
        """
        message0 = Message(
            {"reply_channel": "test-reply-d"},
            None,
            channel_layers[DEFAULT_CHANNEL_LAYER]
        )

        @enforce_ordering
        def inner(message):
            pass

        with self.assertRaises(ValueError):
            inner(message0)

    def test_enforce_ordering_concurrent(self):
        """
        Tests that strict mode of enforce_ordering puts messages in the correct queue after
        the current message number changes while the message is being processed
        """
        # Construct messages to send
        message0 = Message(
            {"reply_channel": "test-reply-e", "order": 0},
            "websocket.connect",
            channel_layers[DEFAULT_CHANNEL_LAYER]
        )
        message2 = Message(
            {"reply_channel": "test-reply-e", "order": 2},
            "websocket.receive",
            channel_layers[DEFAULT_CHANNEL_LAYER]
        )
        message3 = Message(
            {"reply_channel": "test-reply-e", "order": 3},
            "websocket.receive",
            channel_layers[DEFAULT_CHANNEL_LAYER]
        )

        @channel_session
        def add_session(message):
            pass

        # Run them in an acceptable strict order
        @enforce_ordering
        def inner(message):
            pass

        inner(message0)
        inner(message3)

        # Add the session now so it can be mocked
        add_session(message2)

        with mock.patch.object(message2.channel_session, 'load', return_value={'__channels_next_order': 2}):
            inner(message2)

        # Ensure wait channel is empty
        wait_channel = "__wait__.%s" % "test-reply-e"
        next_message = self.get_next_message(wait_channel)
        self.assertEqual(next_message, None)

        # Ensure messages 3 and 2 both ended up back on the original channel
        expected = {
            2: message2,
            3: message3
        }
        for m in range(2):
            message = self.get_next_message("websocket.receive")
            expected.pop(message.content['order'])
        self.assertEqual(expected, {})
