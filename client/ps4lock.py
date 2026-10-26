#!/usr/bin/env python3
"""
ps4lock.py - PC client for the GoldHEN "lockall" parental-control plugin.

Two transports are used:

  1. The plugin's TCP server on the PS4 (default port 9095):
         TOKEN:command\\n  ->  reply
     Commands: lock / unlock / status  (bad token -> "ERR")

  2. GoldHEN's FTP server (default port 2121, anonymous). This is the
     reliable path: the plugin's TCP server only lives inside a running
     game, and a locked game is closed immediately, so when the console is
     locked nothing is listening on 9095. The lock state itself is just a
     tiny file (/data/GoldHEN/lockall_state.txt containing "1" or "0"), so
     lock/unlock/status fall back to FTP, which is always available.

On PS5 with the etaHEN plugin the daemon runs for the whole session, so the
TCP path is always available. If you still use the FTP fallback there, point
it at the etaHEN state file:

    python3 ps4lock.py --ip 192.168.1.50 --state-path /data/etaHEN/lockall_state.txt lock

Defaults - edit these or override on the command line:

    python3 ps4lock.py --ip 192.168.1.50 --token mysecret lock
"""

import argparse
import io
import ftplib
import socket
import sys

# --------------------------------------------------------------------------
# Defaults - edit these
# --------------------------------------------------------------------------
PS4_IP = "192.168.1.50"      # IP of your PS4
PS4_PORT = 9095              # plugin TCP port (LOCK_PORT in the plugin)
FTP_PORT = 2121              # GoldHEN FTP port
FTP_USER = "anonymous"
FTP_PASS = "anonymous"
STATE_PATH = "/data/GoldHEN/lockall_state.txt"
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


def ftp_write_state(ip, ftp_port, state_path, locked, timeout=TIMEOUT):
    """Write the lock state file over FTP (works even when no game is
    running and nothing is listening on the plugin port)."""
    data = b"1\n" if locked else b"0\n"
    with ftplib.FTP() as ftp:
        ftp.connect(ip, ftp_port, timeout=timeout)
        ftp.login(FTP_USER, FTP_PASS)
        ftp.storbinary("STOR " + state_path, io.BytesIO(data))


def ftp_read_state(ip, ftp_port, state_path, timeout=TIMEOUT):
    """Return the lock state as seen on disk: True if locked."""
    buf = io.BytesIO()
    with ftplib.FTP() as ftp:
        ftp.connect(ip, ftp_port, timeout=timeout)
        ftp.login(FTP_USER, FTP_PASS)
        ftp.retrbinary("RETR " + state_path, buf.write)
    return buf.getvalue()[:1] == b"1"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Control the GoldHEN lockall plugin over Wi-Fi.")
    parser.add_argument("command", choices=["lock", "unlock", "status"],
                        help="action to perform")
    parser.add_argument("--ip", default=PS4_IP, help="PS4 IP address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=PS4_PORT, help="plugin port (default: %(default)s)")
    parser.add_argument("--ftp-port", type=int, default=FTP_PORT,
                        help="FTP port for the fallback (default: %(default)s)")
    parser.add_argument("--state-path", default=STATE_PATH,
                        help="state file used by the FTP fallback "
                             "(PS5: /data/etaHEN/lockall_state.txt)")
    parser.add_argument("--token", default=TOKEN, help="auth token (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=TIMEOUT, help="socket timeout (default: %(default)s)")
    args = parser.parse_args(argv)

    try:
        reply = send_command(args.ip, args.port, args.token, args.command, args.timeout)
        print(reply)
        return 1 if reply == "ERR" else 0
    except ConnectionRefusedError:
        pass  # no game running / console locked -> use FTP below
    except socket.timeout:
        print("timeout talking to {}:{} (trying FTP)".format(args.ip, args.port),
              file=sys.stderr)
    except (OSError, ValueError) as exc:
        print("tcp error: {} (trying FTP)".format(exc), file=sys.stderr)

    # ---- FTP fallback: the only transport that is always available --------
    try:
        if args.command == "lock":
            ftp_write_state(args.ip, args.ftp_port, args.state_path, True, args.timeout)
            print("OK locked (via FTP)")
        elif args.command == "unlock":
            ftp_write_state(args.ip, args.ftp_port, args.state_path, False, args.timeout)
            print("OK unlocked (via FTP)")
        else:  # status
            locked = ftp_read_state(args.ip, args.ftp_port, args.state_path, args.timeout)
            print("locked" if locked else "unlocked")
    # ftplib.all_errors already includes OSError
    except ftplib.all_errors as exc:
        print("FTP fallback failed: {}".format(exc), file=sys.stderr)
        print("  - is GoldHEN running with its FTP server (port {}) enabled?".format(args.ftp_port),
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
