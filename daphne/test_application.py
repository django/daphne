import logging
import os
import pickle
import tempfile
from concurrent.futures import CancelledError


class TestApplication:
    """
    An application that receives one or more messages, sends a response,
    and then quits the server. For testing.
    """

    setup_storage = os.path.join(tempfile.gettempdir(), "setup.testio")
    result_storage = os.path.join(tempfile.gettempdir(), "result.testio")

    def __init__(self, scope):
        self.scope = scope
        self.messages = []

    async def __call__(self, send, receive):
        # Receive input and send output
        logging.debug("test app coroutine alive")
        try:
            while True:
                # Receive a message and save it into the result store
                self.messages.append(await receive())
                logging.debug("test app received %r", self.messages[-1])
                self.save_result(self.scope, self.messages)
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
            pickle.dump(
                {
                    "response_messages": response_messages,
                },
                fh,
            )

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
            pickle.dump(
                {
                    "scope": scope,
                    "messages": messages,
                },
                fh,
            )

    @classmethod
    def save_exception(cls, exception):
        """
        Saves details of what happened to the result storage.
        We could use pickle here, but that seems wrong, still, somehow.
        """
        with open(cls.result_storage, "wb") as fh:
            pickle.dump(
                {
                    "exception": exception,
                },
                fh,
            )

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
