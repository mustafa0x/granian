import os
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import httpx
import pytest


pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='requires POSIX signals')


def start_server(port, runtime_mode, timeout):
    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            '-m',
            'granian',
            '--interface',
            'wsgi',
            '--runtime-mode',
            runtime_mode,
            '--workers',
            '1',
            '--port',
            str(port),
            '--graceful-shutdown-timeout',
            str(timeout),
            'tests.apps.wsgi:app',
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        if proc.poll() is not None:
            raise RuntimeError('Granian exited before accepting connections')
        try:
            with closing(socket.create_connection(('127.0.0.1', port), timeout=0.1)):
                return proc
        except OSError:
            time.sleep(0.1)
    raise RuntimeError('Granian did not start')


def request_slow(port, delay, started):
    with httpx.stream('GET', f'http://127.0.0.1:{port}/slow?delay={delay}') as response:
        chunks = response.iter_bytes()
        body = next(chunks)
        started.set()
        return response.status_code, body + b''.join(chunks)


@pytest.fixture
def server_process(server_port, request):
    proc = start_server(server_port, request.param, timeout=1)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


@pytest.mark.parametrize('server_process', ['mt', 'st'], indirect=True)
def test_graceful_shutdown_allows_active_request_to_finish(server_process, server_port):
    started = threading.Event()
    with ThreadPoolExecutor() as executor:
        response = executor.submit(request_slow, server_port, 0.25, started)
        assert started.wait(2)
        server_process.terminate()

        assert response.result(timeout=2) == (200, b'startedfinished')
        assert server_process.wait(timeout=2) == 0


@pytest.mark.parametrize('server_process', ['mt', 'st'], indirect=True)
def test_graceful_shutdown_closes_request_after_timeout(server_process, server_port):
    started = threading.Event()
    with ThreadPoolExecutor() as executor:
        response = executor.submit(request_slow, server_port, 10, started)
        assert started.wait(2)
        server_process.terminate()

        assert server_process.wait(timeout=2) == 0
        with pytest.raises(httpx.HTTPError):
            response.result(timeout=2)
