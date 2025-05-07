import logging
import multiprocessing
import os
import pickle
import tempfile
import traceback
from concurrent.futures import CancelledError


class BaseDaphneTestingInstance:
    """
    Launches an instance of Daphne in a subprocess, with a host and port
    attribute allowing you to call it.

    Works as a context manager.
    """

    startup_timeout = 2

    def __init__(
        self, xff=False, http_timeout=None, request_buffer_size=None, *, application
    ):
        self.xff = xff
        self.http_timeout = http_timeout
        self.host = "127.0.0.1"
        self.request_buffer_size = request_buffer_size
        self.application = application

    def get_application(self):
        return self.application

    def __enter__(self):
        # Option Daphne features
        kwargs = {}
        if self.request_buffer_size:
            kwargs["request_buffer_size"] = self.request_buffer_size
        # Optionally enable X-Forwarded-For support.
        if self.xff:
            kwargs["proxy_forwarded_address_header"] = "X-Forwarded-For"
            kwargs["proxy_forwarded_port_header"] = "X-Forwarded-Port"
            kwargs["proxy_forwarded_proto_header"] = "X-Forwarded-Proto"
        if self.http_timeout:
            kwargs["http_timeout"] = self.http_timeout
        # Start up process
        self.process = DaphneProcess(
            host=self.host,
            get_application=self.get_application,
            kwargs=kwargs,
            setup=self.process_setup,
            teardown=self.process_teardown,
        )
        self.process.start()
        # Wait for the port
        if self.process.ready.wait(self.startup_timeout):
            self.port = self.process.port.value
            return self
        else:
            if self.process.errors.empty():
                raise RuntimeError("Daphne did not start up, no error caught")
            else:
                error, traceback = self.process.errors.get(False)
                raise RuntimeError("Daphne did not start up:\n%s" % traceback)

    def __exit__(self, exc_type, exc_value, traceback):
        # Shut down the process
        self.process.terminate()
        del self.process

    def process_setup(self):
        """
        Called by the process just before it starts serving.
        """
        pass

    def process_teardown(self):
        """
        Called by the process just after it stops serving
        """
        pass

    def get_received(self):
        pass


class DaphneTestingInstance(BaseDaphneTestingInstance):
    def __init__(self, *args, **kwargs):
        self.lock = multiprocessing.Lock()
        super().__init__(*args, **kwargs, application=TestApplication(lock=self.lock))

    def __enter__(self):
        # Clear result storage
        TestApplication.delete_setup()
        TestApplication.delete_result()
        return super().__enter__()

    def get_received(self):
        """
        Returns the scope and messages the test application has received
        so far. Note you'll get all messages since scope start, not just any
        new ones since the last call.

        Also checks for any exceptions in the application. If there are,
        raises them.
        """
        try:
            with self.lock:
                inner_result = TestApplication.load_result()
        except FileNotFoundError:
            raise ValueError("No results available yet.")
        # Check for exception
        if "exception" in inner_result:
            raise inner_result["exception"]
        return inner_result["scope"], inner_result["messages"]

    def add_send_messages(self, messages):
        """
        Adds messages for the application to send back.
        The next time it receives an incoming message, it will reply with these.
        """
        TestApplication.save_setup(response_messages=messages)


class DaphneProcess(multiprocessing.Process):
    """
    Process subclass that launches and runs a Daphne instance, communicating the
    port it ends up listening on back to the parent process.
    """

    def __init__(
        self, host, get_application, kwargs=None, setup=None, teardown=None, port=None
    ):
        super().__init__()
        self.host = host
        self.get_application = get_application
        self.kwargs = kwargs or {}
        self.setup = setup
        self.teardown = teardown
        self.port = multiprocessing.Value("i", port if port is not None else 0)
        self.ready = multiprocessing.Event()
        self.errors = multiprocessing.Queue()

    def run(self):
        # OK, now we are in a forked child process, and want to use the reactor.
        # However, FreeBSD systems like MacOS do not fork the underlying Kqueue,
        # which asyncio (hence asyncioreactor) is built on.
        # Therefore, we should uninstall the broken reactor and install a new one.
        _reinstall_reactor()

        from twisted.internet import reactor

        from .endpoints import build_endpoint_description_strings
        from .server import Server

        application = self.get_application()

        try:
            # Create the server class
            endpoints = build_endpoint_description_strings(
                host=self.host, port=self.port.value
            )
            self.server = Server(
                application=application,
                endpoints=endpoints,
                signal_handlers=False,
                **self.kwargs,
            )
            # Set up a poller to look for the port
            reactor.callLater(0.1, self.resolve_port)
            # Run with setup/teardown
            if self.setup is not None:
                self.setup()
            try:
                self.server.run()
            finally:
                if self.teardown is not None:
                    self.teardown()
        except BaseException as e:
            # Put the error on our queue so the parent gets it
            self.errors.put((e, traceback.format_exc()))

    def resolve_port(self):
        from twisted.internet import reactor

        if self.server.listening_addresses:
            self.port.value = self.server.listening_addresses[0][1]
            self.ready.set()
        else:
            reactor.callLater(0.1, self.resolve_port)


class TestApplication:
    """
    An application that receives one or more messages, sends a response,
    and then quits the server. For testing.
    """

    setup_storage = os.path.join(tempfile.gettempdir(), "setup.testio")
    result_storage = os.path.join(tempfile.gettempdir(), "result.testio")

    def __init__(self, lock):
        self.lock = lock
        self.messages = []

    async def __call__(self, scope, receive, send):
        self.scope = scope
        # Receive input and send output
        logging.debug("test app coroutine alive")
        try:
            while True:
                # Receive a message and save it into the result store
                self.messages.append(await receive())
                self.lock.acquire()
                logging.debug("test app received %r", self.messages[-1])
                self.save_result(self.scope, self.messages)
                self.lock.release()
                # See if there are any messages to send back
                setup = self.load_setup()
                self.delete_setup()
                for message in setup["response_messages"]:
                    await send(message)
                    logging.debug("test app sent %r", message)
        except Exception as e:
            if isinstance(e, CancelledError):
                # Don't catch task-cancelled errors!
                raise
            else:
                self.save_exception(e)

    @classmethod
    def save_setup(cls, response_messages):
        """
        Stores setup information.
        """
        with open(cls.setup_storage, "wb") as fh:
            pickle.dump({"response_messages": response_messages}, fh)

    @classmethod
    def load_setup(cls):
        """
        Returns setup details.
        """
        try:
            with open(cls.setup_storage, "rb") as fh:
                return pickle.load(fh)
        except FileNotFoundError:
            return {"response_messages": []}

    @classmethod
    def save_result(cls, scope, messages):
        """
        Saves details of what happened to the result storage.
        We could use pickle here, but that seems wrong, still, somehow.
        """
        with open(cls.result_storage, "wb") as fh:
            pickle.dump({"scope": scope, "messages": messages}, fh)

    @classmethod
    def save_exception(cls, exception):
        """
        Saves details of what happened to the result storage.
        We could use pickle here, but that seems wrong, still, somehow.
        """
        with open(cls.result_storage, "wb") as fh:
            pickle.dump({"exception": exception}, fh)

    @classmethod
    def load_result(cls):
        """
        Returns result details.
        """
        with open(cls.result_storage, "rb") as fh:
            return pickle.load(fh)

    @classmethod
    def delete_setup(cls):
        """
        Clears setup storage files.
        """
        try:
            os.unlink(cls.setup_storage)
        except OSError:
            pass

    @classmethod
    def delete_result(cls):
        """
        Clears result storage files.
        """
        try:
            os.unlink(cls.result_storage)
        except OSError:
            pass


def _reinstall_reactor():
    import asyncio
    import sys

    from twisted.internet import asyncioreactor

    # Uninstall the reactor.
    if "twisted.internet.reactor" in sys.modules:
        del sys.modules["twisted.internet.reactor"]

    # The daphne.server module may have already installed the reactor.
    # If so, using this module will use uninstalled one, thus we should
    # reimport this module too.
    if "daphne.server" in sys.modules:
        del sys.modules["daphne.server"]

    event_loop = asyncio.new_event_loop()
    asyncioreactor.install(event_loop)
    asyncio.set_event_loop(event_loop)
