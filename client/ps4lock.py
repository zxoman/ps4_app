#!/usr/bin/env python3
"""
ps4lock.py - PC client for the GoldHEN "lockall" parental-control plugin.

It talks to the plugin's TCP server on the PS4 (default port 9095) using the
simple line protocol:

    TOKEN:command\\n        ->  reply (no newline required to read)

Commands:
    lock     -> "OK locked"
    unlock   -> "OK unlocked"
    status   -> "locked" / "unlocked"
    anything with a bad token -> "ERR"

Edit the four variables below, or override them on the command line:

    python3 ps4lock.py --ip 192.168.1.50 --token mysecret lock
"""

import argparse
import socket
import sys

# --------------------------------------------------------------------------
# Defaults - edit these
# --------------------------------------------------------------------------
PS4_IP = "192.168.1.50"      # IP of your PS4
PS4_PORT = 9095              # must match LOCK_PORT in the plugin
TOKEN = "changeme"           # must match /data/GoldHEN/lockall.ini
TIMEOUT = 5.0                # socket timeout in seconds
# --------------------------------------------------------------------------


def send_command(ip, port, token, command, timeout=TIMEOUT):
    """Open a connection, send 'token:command', return the text reply."""
    payload = "{}:{}\n".format(token, command).encode("utf-8")
    if len(payload) > 128:
        raise ValueError("command too long (plugin caps at 128 bytes)")

    with socket.create_connection((ip, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload)
        chunks = []
        while True:
            try:
                data = sock.recv(256)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            # replies are short and terminated by the server closing the
            # socket; stop as soon as we have something that looks complete
            if b"\n" in data:
                break
        return b"".join(chunks).decode("utf-8", errors="replace").strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Control the GoldHEN lockall plugin over Wi-Fi.")
    parser.add_argument("command", choices=["lock", "unlock", "status"],
                        help="action to perform")
    parser.add_argument("--ip", default=PS4_IP, help="PS4 IP address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=PS4_PORT, help="plugin port (default: %(default)s)")
    parser.add_argument("--token", default=TOKEN, help="auth token (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=TIMEOUT, help="socket timeout (default: %(default)s)")
    args = parser.parse_args(argv)

    try:
        reply = send_command(args.ip, args.port, args.token, args.command, args.timeout)
    except ConnectionRefusedError:
        print("connection refused: plugin not listening on {}:{}".format(args.ip, args.port),
              file=sys.stderr)
        print("  - is lockall.prx installed in /data/GoldHEN/plugins/ and listed in plugins.ini?",
              file=sys.stderr)
        print("  - is a GoldHEN-injected process (a game/app) currently running?",
              file=sys.stderr)
        return 2
    except socket.timeout:
        print("timeout talking to {}:{}".format(args.ip, args.port), file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2

    print(reply)
    if reply == "ERR":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
