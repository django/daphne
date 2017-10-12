Channels Concepts
=================

Django's traditional view of the world revolves around requests and responses;
a request comes in, Django is fired up to serve it, generates a response to
send, and then Django goes away and waits for the next request.

That was fine when the internet was driven by simple browser interactions,
but the modern Web includes things like WebSockets and HTTP2 server push,
which allow websites to communicate outside of this traditional cycle.

And, beyond that, there are plenty of non-critical tasks that applications
could easily offload until after a response has been sent - like saving things
into a cache or thumbnailing newly-uploaded images.

It changes the way Django runs to be "event oriented" - rather than
just responding to requests, instead Django responds to a wide array of events
sent on *channels*. There's still no persistent state - each event handler,
or *consumer* as we call them, is called independently in a way much like a
view is called.

Let's look at what *channels* are first.

.. _what-are-channels:

What is a channel?
------------------

The core of the system is, unsurprisingly, a datastructure called a *channel*.
What is a channel? It is an *ordered*, *first-in first-out queue* with
*message expiry* and *at-most-once delivery* to *only one listener at a time*.

You can think of it as analogous to a task queue - messages are put onto
the channel by *producers*, and then given to just one of the *consumers*
listening to that channel.

By *at-most-once* we say that either one consumer gets the message or nobody
does (if the channel implementation crashes, let's say). The
alternative is *at-least-once*, where normally one consumer gets the message
but when things crash it's sent to more than one, which is not the trade-off
we want.

There are a couple of other limitations - messages must be made of
serializable types, and stay under a certain size limit - but these are
implementation details you won't need to worry about until you get to more
advanced usage.

The channels have capacity, so a lot of producers can write lots of messages
into a channel with no consumers and then a consumer can come along later and
will start getting served those queued messages.

If you've used `channels in Go <https://gobyexample.com/channels>`_: Go channels 
are reasonably similar to Django ones. The key difference is that 
Django channels are network-transparent; the implementations
of channels we provide are all accessible across a network to consumers
and producers running in different processes or on different machines.

Inside a network, we identify channels uniquely by a name string - you can
send to any named channel from any machine connected to the same channel 
backend. If two different machines both write to the ``http.request``
channel, they're writing into the same channel.

How do we use channels?
-----------------------

So how is Django using those channels? Inside Django
you can write a function to consume a channel::

    def my_consumer(message):
        pass

And then assign a channel to it in the channel routing::

    channel_routing = {
        "some-channel": "myapp.consumers.my_consumer",
    }

This means that for every message on the channel, Django will call that
consumer function with a message object (message objects have a "content"
attribute which is always a dict of data, and a "channel" attribute which
is the channel it came from, as well as some others).

Instead of having Django run in the traditional request-response mode, 
Channels changes Django so that it runs in a worker mode - it listens on 
all channels that have consumers assigned, and when a message arrives on
one, it runs the relevant consumer. So rather than running in just a 
single process tied to a WSGI server, Django runs in three separate layers:

* Interface servers, which communicate between Django and the outside world.
  This includes a WSGI adapter as well as a separate WebSocket server - this is explained and covered in :ref:`run-interface-servers`.

* The channel backend, which is a combination of pluggable Python code and
  a datastore (e.g. Redis, or a shared memory segment) responsible for
  transporting messages.

* The workers, that listen on all relevant channels and run consumer code
  when a message is ready.

This may seem relatively simplistic, but that's part of the design; rather than
try and have a full asynchronous architecture, we're just introducing a
slightly more complex abstraction than that presented by Django views.

A view takes a request and returns a response; a consumer takes a channel
message and can write out zero to many other channel messages.

Now, let's make a channel for requests (called ``http.request``),
and a channel per client for responses (e.g. ``http.response.o4F2h2Fd``),
where the response channel is a property (``reply_channel``) of the request
message. Suddenly, a view is merely another example of a consumer::

    # Listens on http.request
    def my_consumer(message):
        # Decode the request from message format to a Request object
        django_request = AsgiRequest(message)
        # Run view
        django_response = view(django_request)
        # Encode the response into message format
        for chunk in AsgiHandler.encode_response(django_response):
            message.reply_channel.send(chunk)

In fact, this is how Channels works. The interface servers transform connections
from the outside world (HTTP, WebSockets, etc.) into messages on channels,
and then you write workers to handle these messages. Usually you leave normal
HTTP up to Django's built-in consumers that plug it into the view/template
system, but you can override it to add functionality if you want.

However, the crucial part is that you can run code (and so send on channels) in
response to any event - and that includes ones you create. You can trigger
on model saves, on other incoming messages, or from code paths inside views
and forms. That approach comes in handy for push-style
code - where you use WebSockets or HTTP long-polling to notify
clients of changes in real time (messages in a chat, perhaps, or live updates
in an admin as another user edits something).

.. _channel-types:

Channel Types
-------------

There are actually two major uses for channels in
this model. The first, and more obvious one, is the dispatching of work to
consumers - a message gets added to a channel, and then any one of the workers
can pick it up and run the consumer.

The second kind of channel, however, is used for replies. Notably, these only
have one thing listening on them - the interface server. Each reply channel
is individually named and has to be routed back to the interface server where
its client is terminated.

This is not a massive difference - they both still behave according to the core
definition of a *channel* - but presents some problems when we're looking to
scale things up. We can happily randomly load-balance normal channels across
clusters of channel servers and workers - after all, any worker can process
the message - but response channels would have to have their messages sent
to the channel server they're listening on.

For this reason, Channels treats these as two different *channel types*, and
denotes a *reply channel* by having the channel name contain
the character ``!`` - e.g. ``http.response!f5G3fE21f``. *Normal
channels* do not contain it, but along with the rest of the reply
channel name, they must contain only the characters ``a-z A-Z 0-9 - _``,
and be less than 200 characters long.

It's optional for a backend implementation to understand this - after all,
it's only important at scale, where you want to shard the two types differently
— but it's present nonetheless. For more on scaling, and how to handle channel
types if you're writing a backend or interface server, see :ref:`scaling-up`.

Groups
------

Because channels only deliver to a single listener, they can't do broadcast;
if you want to send a message to an arbitrary group of clients, you need to
keep track of which reply channels of those you wish to send to.

If I had a liveblog where I wanted to push out updates whenever a new post is
saved, I could register a handler for the ``post_save`` signal and keep a
set of channels (here, using Redis) to send updates to::

    redis_conn = redis.Redis("localhost", 6379)

    @receiver(post_save, sender=BlogUpdate)
    def send_update(sender, instance, **kwargs):
        # Loop through all reply channels and send the update
        for reply_channel in redis_conn.smembers("readers"):
            Channel(reply_channel).send({
                "text": json.dumps({
                    "id": instance.id,
                    "content": instance.content
                })
            })

    # Connected to websocket.connect
    def ws_connect(message):
        # Add to reader set
        redis_conn.sadd("readers", message.reply_channel.name)

While this will work, there's a small problem - we never remove people from
the ``readers`` set when they disconnect. We could add a consumer that
listens to ``websocket.disconnect`` to do that, but we'd also need to
have some kind of expiry in case an interface server is forced to quit or
loses power before it can send disconnect signals - your code will never
see any disconnect notification but the reply channel is completely
invalid and messages you send there will sit there until they expire.

Because the basic design of channels is stateless, the channel server has no
concept of "closing" a channel if an interface server goes away - after all,
channels are meant to hold messages until a consumer comes along (and some
types of interface server, e.g. an SMS gateway, could theoretically serve
any client from any interface server).

We don't particularly care if a disconnected client doesn't get the messages
sent to the group - after all, it disconnected - but we do care about
cluttering up the channel backend tracking all of these clients that are no
longer around (and possibly, eventually getting a collision on the reply
channel name and sending someone messages not meant for them, though that would
likely take weeks).

Now, we could go back into our example above and add an expiring set and keep
track of expiry times and so forth, but what would be the point of a framework
if it made you add boilerplate code? Instead, Channels implements this
abstraction as a core concept called Groups::

    @receiver(post_save, sender=BlogUpdate)
    def send_update(sender, instance, **kwargs):
        Group("liveblog").send({
            "text": json.dumps({
                "id": instance.id,
                "content": instance.content
            })
        })

    # Connected to websocket.connect
    def ws_connect(message):
        # Add to reader group
        Group("liveblog").add(message.reply_channel)
        # Accept the connection request
        message.reply_channel.send({"accept": True})

    # Connected to websocket.disconnect
    def ws_disconnect(message):
        # Remove from reader group on clean disconnect
        Group("liveblog").discard(message.reply_channel)

Not only do groups have their own ``send()`` method (which backends can provide
an efficient implementation of), they also automatically manage expiry of
the group members - when the channel starts having messages expire on it due
to non-consumption, we go in and remove it from all the groups it's in as well.
Of course, you should still remove things from the group on disconnect if you
can; the expiry code is there to catch cases where the disconnect message
doesn't make it for some reason.

Groups are generally only useful for reply channels (ones containing
the character ``!``), as these are unique-per-client, but can be used for
normal channels as well if you wish.

Next Steps
----------

That's the high-level overview of channels and groups, and how you should
start thinking about them. Remember, Django provides some channels
but you're free to make and consume your own, and all channels are
network-transparent.

One thing channels do not do, however, is guarantee delivery. If you need
certainty that tasks will complete, use a system designed for this with 
retries and persistence (e.g. Celery), or alternatively make a management
command that checks for completion and re-submits a message to the channel
if nothing is completed (rolling your own retry logic, essentially).

We'll cover more about what kind of tasks fit well into Channels in the rest
of the documentation, but for now, let's progress to :doc:`getting-started`
and writing some code.
