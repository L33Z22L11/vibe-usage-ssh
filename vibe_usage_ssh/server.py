#!/usr/bin/env python3
"""Dedicated SSH transport for the read-only Textual application."""

import asyncio
import fcntl
import logging
import os
import pty
import signal
import struct
import sys
import termios

import asyncssh

from .config import api_credentials, host_key_path
from .preferences import client_id

PORT = int(os.environ.get("VIBE_SSH_PORT", "5163"))
HOST = os.environ.get("VIBE_SSH_HOST", "127.0.0.1")
LIMIT = 20
active = 0


def ensure_host_key():
    path = host_key_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = asyncssh.generate_private_key("ssh-ed25519")
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(key.export_private_key())
    return str(path)


class UsageServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return False


def resize(fd, size, pid=None):
    cols, rows, px, py = size
    cols, rows = max(20, min(cols or 80, 500)), max(8, min(rows or 24, 250))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, py, px))
    if pid:
        try:
            os.killpg(pid, signal.SIGWINCH)
        except ProcessLookupError:
            pass


async def handle(process):
    global active
    if process.command or process.subsystem or not process.term_type or active >= LIMIT:
        process.exit(1)
        return
    active += 1
    master, slave = pty.openpty()
    child = None
    log_fd = None
    reader_installed = False
    try:
        resize(slave, process.term_size)
        env = os.environ.copy()
        env.pop("NO_COLOR", None)
        env.update(TERM=process.term_type, COLORTERM="truecolor", PYTHONUNBUFFERED="1")
        env["VIBE_SSH_CLIENT_ID"] = client_id(
            process.get_extra_info("peername"),
            process.get_extra_info("client_version"),
            process.get_extra_info("username"),
        )
        # Keep UI diagnostics in the service log instead of the client's PTY.
        log_fd = os.dup(sys.stderr.fileno())
        env["VIBE_SSH_LOG_FD"] = str(log_fd)
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "vibe_usage_ssh.ui",
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            pass_fds=(log_fd,),
            start_new_session=True,
        )
        os.close(log_fd)
        log_fd = None
        os.close(slave)
        slave = -1
        loop = asyncio.get_running_loop()

        def read_pty():
            try:
                chunk = os.read(master, 65536)
                if chunk:
                    process.stdout.write(chunk)
                else:
                    loop.remove_reader(master)
            except (OSError, asyncssh.Error):
                loop.remove_reader(master)

        loop.add_reader(master, read_pty)
        reader_installed = True

        async def input_pump():
            while True:
                try:
                    data = await process.stdin.read(8192)
                    if not data:
                        break
                    os.write(master, data)
                except asyncssh.TerminalSizeChanged as exc:
                    resize(master, exc.term_size, child.pid)
                except (OSError, asyncssh.Error):
                    break

        pump = asyncio.create_task(input_pump())
        _, pending = await asyncio.wait(
            [pump, asyncio.create_task(child.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except (OSError, asyncssh.Error) as exc:
        logging.warning("Session error: %s", type(exc).__name__)
    finally:
        if log_fd is not None:
            os.close(log_fd)
        if reader_installed:
            asyncio.get_running_loop().remove_reader(master)
        if slave >= 0:
            os.close(slave)
        os.close(master)
        if child and child.returncode is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            await child.wait()
        active -= 1
        process.exit(0)


async def main():
    server = await asyncssh.create_server(
        UsageServer,
        HOST,
        PORT,
        server_host_keys=[ensure_host_key()],
        process_factory=handle,
        encoding=None,
        line_editor=False,
    )
    logging.info("Textual SSH listening on %s:%d", HOST, PORT)
    await server.wait_closed()


def run():
    api_credentials()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
