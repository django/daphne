import os
import socket
import weakref
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

import test_http_response
from http_base import DaphneTestCase

__all__ = ["UnixSocketFDDaphneTestCase", "TestInheritedUnixSocket"]


class UnixSocketFDDaphneTestCase(DaphneTestCase):
    @property
    def _instance_endpoint_args(self):
        tmp_dir = TemporaryDirectory()
        weakref.finalize(self, tmp_dir.cleanup)
        sock_path = str(Path(tmp_dir.name, "test.sock"))
        listen_sock = socket.socket(socket.AF_UNIX, type=socket.SOCK_STREAM)
        listen_sock.bind(sock_path)
        listen_sock.listen()
        listen_sock_fileno = os.dup(listen_sock.fileno())
        os.set_inheritable(listen_sock_fileno, True)
        listen_sock.close()
        return {"host": None, "file_descriptor": listen_sock_fileno}

    @staticmethod
    def _get_instance_socket_path(test_app):
        with socket.socket(fileno=os.dup(test_app.file_descriptor)) as sock:
            return sock.getsockname()

    @classmethod
    def _get_instance_raw_socket_connection(cls, test_app, *, timeout):
        socket_name = cls._get_instance_socket_path(test_app)
        s = socket.socket(socket.AF_UNIX, type=socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(socket_name)
        return s

    @classmethod
    def _get_instance_http_connection(cls, test_app, *, timeout):
        def connect():
            conn.sock = cls._get_instance_raw_socket_connection(
                test_app, timeout=timeout
            )

        conn = HTTPConnection("", timeout=timeout)
        conn.connect = connect
        return conn


@skipUnless(hasattr(socket, "AF_UNIX"), "AF_UNIX support not present.")
class TestInheritedUnixSocket(UnixSocketFDDaphneTestCase):
    test_minimal_response = test_http_response.TestHTTPResponse.test_minimal_response
