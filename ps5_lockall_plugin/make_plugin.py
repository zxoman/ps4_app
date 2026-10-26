#!/usr/bin/env python3
"""Prepend the etaHEN plugin header to a PS5 ELF.

Header format (from etaHEN-Plugins/lib/make_plugin.py):
    etaHEN_PLUGIN\0<TID>\0<VERSION>\0
TID must be 4 letters + 5 digits (e.g. LOCK00001); version x.xx (e.g. 1.00).
"""
import re
import sys

HEADER_PREFIX = "etaHEN_PLUGIN"


def is_valid_version(version):
    return re.match(r"^\d\.\d{2}$", version) is not None


def is_valid_tid(tid):
    return re.match(r"^[A-Za-z]{4}\d{5}$", tid) is not None


def verify_header(file_name, prefix_length):
    with open(file_name, "rb") as f:
        if f.read(prefix_length).startswith(HEADER_PREFIX.encode()):
            print("Verification: header correctly added.")
        else:
            print("Verification: header NOT found.")


def add_header(elf_filename, tid, version):
    header = f"{HEADER_PREFIX}\x00{tid}\x00{version}\x00"
    base = elf_filename.rsplit(".elf", 1)[0] if ".elf" in elf_filename else elf_filename
    new_filename = base + ".plugin"

    with open(elf_filename, "rb") as f:
        contents = f.read()
    with open(new_filename, "wb") as f:
        f.write(header.encode() + contents)

    verify_header(new_filename, len(HEADER_PREFIX))
    print(f"Wrote {new_filename} (TID={tid}, version={version})")
    return new_filename


def main():
    if len(sys.argv) < 4:
        print("Usage: make_plugin.py <elf> <TID> <version>")
        return 1
    elf_filename, tid, version = sys.argv[1:4]
    if not is_valid_tid(tid):
        print(f"Error: TID must be 4 letters + 5 digits, got {tid!r}")
        return 1
    if not is_valid_version(version):
        print(f"Error: version must be x.xx, got {version!r}")
        return 1
    add_header(elf_filename, tid, version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
