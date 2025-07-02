import sys

__version__ = "4.2.1"


# Windows on Python 3.8+ uses ProactorEventLoop, which is not compatible with
# Twisted. Does not implement add_writer/add_reader.
# See https://bugs.python.org/issue37373
# and https://twistedmatrix.com/trac/ticket/9766
PY38_WIN = sys.version_info >= (3, 8) and sys.platform == "win32"
if PY38_WIN:
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
