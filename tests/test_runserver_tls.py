import http.client
import json
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from twisted.web.http import H2_ENABLED


@pytest.fixture
def project(tmp_path):
    source = Path(__file__).with_name("runserver_project.py")
    target = tmp_path / source.name
    shutil.copyfile(source, target)
    static = tmp_path / "static"
    static.mkdir()
    (static / "hello.txt").write_text("development static")
    return target


class Runserver:
    def __init__(self, process, port, context, log):
        self.process = process
        self.port = port
        self.context = context
        self.log = log

    def request(self, path="/"):
        if self.context:
            conn = http.client.HTTPSConnection(
                "127.0.0.1", self.port, context=self.context, timeout=1
            )
        else:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return (
                response.status,
                {key.lower(): value for key, value in response.getheaders()},
                response.read(),
            )
        finally:
            conn.close()

    def wait_for_response(self, message="first"):
        deadline = time.monotonic() + 30
        last_error = None
        while time.monotonic() < deadline:
            assert self.process.poll() is None, self.log.read_text()[-10000:]
            try:
                status, headers, body = self.request()
                data = json.loads(body)
                if data["message"] == message:
                    assert status == 200
                    assert headers["x-asgi-middleware"] == "configured"
                    return data
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
            time.sleep(0.05)
        pytest.fail(
            "runserver did not respond as expected (last error: %r):\n%s"
            % (last_error, self.log.read_text()[-10000:])
        )

    def wait_for_log(self, text):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            assert self.process.poll() is None, self.log.read_text()[-10000:]
            if text in self.log.read_text():
                return
            time.sleep(0.05)
        pytest.fail(
            "runserver did not log %r:\n%s" % (text, self.log.read_text()[-10000:])
        )


@pytest.fixture
def runserver(project, certificates, tmp_path):
    @contextmanager
    def start(*, tls=True, reloader=False):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        args = [sys.executable, "-u", str(project), "runserver", "127.0.0.1:%d" % port]
        context = None
        if tls:
            certfile, keyfile = certificates
            args += ["--certfile", str(certfile), "--keyfile", str(keyfile)]
            context = ssl.create_default_context(cafile=str(certfile))
        if not reloader:
            args.append("--noreload")
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        # The project runs from tmp_path, using the source checkout under test.
        env["PYTHONPATH"] = os.pathsep.join(
            [str(Path(__file__).resolve().parent.parent), env.get("PYTHONPATH", "")]
        )
        env.pop("RUN_MAIN", None)
        log = tmp_path / "runserver.log"
        with log.open("w") as output:
            process = subprocess.Popen(
                args,
                cwd=project.parent,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
            )
            try:
                yield Runserver(process, port, context, log)
            finally:
                # Django's reloader starts a child. Stop the entire group/tree,
                # including when a request or startup assertion fails.
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    try:
                        os.killpg(process.pid, signal.SIGINT)
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=5)
        # Verify that shutdown released the listener, including reloaded children.
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))

    return start


@pytest.mark.parametrize("tls", [False, True])
@pytest.mark.parametrize("reloader", [False, True])
def test_requests_and_lifecycle(runserver, project, tls, reloader):
    with runserver(tls=tls, reloader=reloader) as server:
        data = server.wait_for_response()
        assert data["scheme"] == ("https" if tls else "http")
        assert data["secure"] is tls
        assert data["http_version"] == "1.1"
        assert server.request("/static/hello.txt")[::2] == (200, b"development static")
        if reloader:
            # Wait until StatReloader has recorded the original timestamp.
            server.wait_for_log("File %s first seen with mtime" % project)
            project.write_text(
                project.read_text().replace('MESSAGE = "first"', 'MESSAGE = "second"')
            )
            # Advance mtime even on filesystems with coarse timestamp resolution.
            modified = project.stat().st_mtime + 2
            os.utime(project, (modified, modified))
            reloaded = server.wait_for_response("second")
            assert reloaded["pid"] != data["pid"]
            assert reloaded["scheme"] == data["scheme"]
            assert reloaded["secure"] is tls
        log = server.log.read_text()
        assert "System check identified no issues" in log
        assert "unapplied migration(s)" in log
        url = "%s://127.0.0.1:%d/" % ("https" if tls else "http", server.port)
        assert url in log
        if reloader:
            assert log.count("System check identified no issues") >= 2


def test_secure_websocket_routing(runserver):
    with runserver() as server:
        server.wait_for_response()
        with socket.create_connection(("127.0.0.1", server.port), timeout=3) as tcp:
            with server.context.wrap_socket(tcp, server_hostname="localhost") as sock:
                sock.sendall(
                    b"GET /ws/ HTTP/1.1\r\nHost: localhost\r\n"
                    b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    b"Sec-WebSocket-Key: x3JJHMbDL1EzLkh9GBhXDw==\r\n"
                    b"Sec-WebSocket-Version: 13\r\n\r\n"
                )
                with sock.makefile("rb") as response:
                    assert response.readline().startswith(b"HTTP/1.1 101")
                    while True:
                        header = response.readline()
                        assert header, "Connection closed during WebSocket handshake"
                        if header == b"\r\n":
                            break
                    # One masked text frame, echoed by the configured route.
                    sock.sendall(b"\x81\x81\x00\x00\x00\x00x")
                    assert response.read(3) == b"\x81\x01x"


@pytest.mark.skipif(not H2_ENABLED, reason="requires Twisted[http2,tls]")
def test_http2_negotiation_and_request(runserver):
    h2_connection = pytest.importorskip("h2.connection")
    h2_events = pytest.importorskip("h2.events")
    with runserver() as server:
        server.wait_for_response()
        server.context.set_alpn_protocols(["h2"])
        with socket.create_connection(("127.0.0.1", server.port), timeout=3) as tcp:
            with server.context.wrap_socket(tcp, server_hostname="localhost") as sock:
                assert sock.selected_alpn_protocol() == "h2"
                connection = h2_connection.H2Connection()
                connection.initiate_connection()
                connection.send_headers(
                    1,
                    [
                        (":method", "GET"),
                        (":scheme", "https"),
                        (":authority", "localhost"),
                        (":path", "/"),
                    ],
                    end_stream=True,
                )
                sock.sendall(connection.data_to_send())
                headers, body, complete = {}, b"", False
                while not complete:
                    received = sock.recv(65536)
                    assert (
                        received
                    ), "HTTP/2 connection closed before response completed"
                    for event in connection.receive_data(received):
                        if isinstance(event, h2_events.ResponseReceived):
                            headers.update(event.headers)
                        elif isinstance(event, h2_events.DataReceived):
                            body += event.data
                            connection.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, h2_events.StreamEnded):
                            complete = True
                    sock.sendall(connection.data_to_send())
                assert headers[b":status"] == b"200"
                assert headers[b"x-asgi-middleware"] == b"configured"
                data = json.loads(body)
                assert data["scheme"] == "https"
                assert data["secure"] is True
                assert data["http_version"] == "2"
