from daphne.testing import DaphneProcess, TestApplication


def test_daphne_process():
    """
    Minimal test of DaphneProcess.
    """
    application = TestApplication
    server_process = DaphneProcess("localhost", application)
    server_process.start()
    server_process.ready.wait()
    port = server_process.port.value
    server_process.terminate()
    server_process.join()

    assert port > 0, "Port was not set"
