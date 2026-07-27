# Graceful shutdown timeout: reproduction notes

These commands reproduce the unbounded shutdown and verify the proposed
`--graceful-shutdown-timeout` behavior. The recorded measurements were made
with Granian 2.7.9 on x86_64 Linux.

## Build and automated tests

```sh
uv sync --group all
uv run maturin develop --uv
uv run pytest -v tests/test_shutdown.py
```

The tests cover both runtime modes. One request finishes within the grace
period and must retain its complete HTTP 200 response. Another exceeds the
grace period and must be disconnected while Granian still exits normally.

## Manual reproduction

Start Granian without a connection timeout:

```sh
.venv/bin/granian \
  --interface wsgi \
  --runtime-mode mt \
  --workers 2 \
  --host 127.0.0.1 \
  --port 3913 \
  --pid-file /tmp/granian-graceful-repro.pid \
  tests.apps.wsgi:app
```

Start a long request in another terminal:

```sh
curl --no-buffer 'http://127.0.0.1:3913/slow?delay=30'
```

Send `SIGTERM` to the Granian parent process:

```sh
kill -TERM "$(cat /tmp/granian-graceful-repro.pid)"
```

It will continue waiting for the request because the default graceful shutdown
timeout is disabled.

Repeat with a three-second bound:

```sh
.venv/bin/granian \
  --interface wsgi \
  --runtime-mode mt \
  --workers 2 \
  --host 127.0.0.1 \
  --port 3913 \
  --pid-file /tmp/granian-graceful-repro.pid \
  --graceful-shutdown-timeout 3 \
  tests.apps.wsgi:app
```

After `SIGTERM`, the client is disconnected after about three seconds and
Granian exits normally. Changing the request to `delay=2` lets it finish with
HTTP 200 before Granian exits.

## Linux systemd check

The production-like check used a transient unit so no installed service was
changed:

```sh
sudo systemd-run \
  --unit=granian-graceful-repro \
  --property=User="$USER" \
  --property=WorkingDirectory="$PWD" \
  --property=TimeoutStopSec=8s \
  --property=KillMode=control-group \
  "$PWD/.venv/bin/granian" \
  --interface wsgi \
  --workers 2 \
  --host 127.0.0.1 \
  --port 3913 \
  --graceful-shutdown-timeout 3 \
  tests.apps.wsgi:app

curl --no-buffer 'http://127.0.0.1:3913/slow?delay=30'
sudo systemctl stop granian-graceful-repro.service
sudo journalctl -u granian-graceful-repro.service --no-pager
```

With plain 2.7.9 and no Granian timeout, systemd reached its eight-second stop
limit and sent `SIGKILL`. With this change and a three-second timeout, Granian
stopped cleanly in 3.033 seconds. A two-second request stopped cleanly in 1.520
seconds and returned HTTP 200.

For comparison, `--workers-kill-timeout 3` also bounded shutdown, but did so by
killing the worker. The connection timeout allowed requests within the grace
period to finish and closed only those that exceeded it.

To reproduce the plain 2.7.9 result, omit
`--graceful-shutdown-timeout 3` from the transient-unit command.

## Linux wheel build used for verification

```sh
docker run --rm \
  -v "$PWD:/io" \
  ghcr.io/pyo3/maturin:latest \
  build --release --interpreter python3.12
```

The resulting wheel was installed into a clean Python 3.12 virtual environment
before running the transient-unit checks above.
