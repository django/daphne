import os
import pickle
import tempfile


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
        # Load setup info
        setup = self.load_setup()
        # Receive input and send output
        try:
            for _ in range(setup["receive_messages"]):
                self.messages.append(await receive())
            for message in setup["response_messages"]:
                await send(message)
        except Exception as e:
            self.save_exception(e)
        else:
            self.save_result()

    @classmethod
    def save_setup(cls, response_messages, receive_messages=1):
        """
        Stores setup information.
        """
        with open(cls.setup_storage, "wb") as fh:
            pickle.dump(
                {
                    "response_messages": response_messages,
                    "receive_messages": receive_messages,
                },
                fh,
            )

    @classmethod
    def load_setup(cls):
        """
        Returns setup details.
        """
        with open(cls.setup_storage, "rb") as fh:
            return pickle.load(fh)

    def save_result(self):
        """
        Saves details of what happened to the result storage.
        We could use pickle here, but that seems wrong, still, somehow.
        """
        with open(self.result_storage, "wb") as fh:
            pickle.dump(
                {
                    "scope": self.scope,
                    "messages": self.messages,
                },
                fh,
            )

    def save_exception(self, exception):
        """
        Saves details of what happened to the result storage.
        We could use pickle here, but that seems wrong, still, somehow.
        """
        with open(self.result_storage, "wb") as fh:
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
    def clear_storage(cls):
        """
        Clears storage files.
        """
        try:
            os.unlink(cls.setup_storage)
        except OSError:
            pass
        try:
            os.unlink(cls.result_storage)
        except OSError:
            pass
