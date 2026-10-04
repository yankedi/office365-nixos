#!/usr/bin/env python3
"""Explicit Wine integration check; uses a temporary prefix and a local HTTP server."""

import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time


class RangeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, fail_range=False, **kwargs):
        self.fail_range = fail_range
        super().__init__(*args, **kwargs)

    def log_message(self, *args):
        pass

    def do_GET(self):
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
        if not match:
            self.send_error(400)
            return
        start, end = map(int, match.groups())
        length = end - start + 1
        if length != 65536 or not 33554432 <= start < 100663296:
            self.send_error(416)
            return
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/536870912")
        self.send_header("Content-Length", str(length))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            if self.fail_range and start == 33554432 + 3 * 1048576:
                time.sleep(0.15)
                self.wfile.write(b"A" * 16384)
            else:
                time.sleep(0.8 if self.fail_range else 0.01)
                self.wfile.write(b"B" * length)
            self.wfile.flush()
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True, help="Built office365-nixos package")
    parser.add_argument("--probe", type=Path, required=True, help="Built download-probe.exe")
    parser.add_argument("--logs", type=Path, required=True, help="Directory to retain test logs")
    args = parser.parse_args()
    args.logs = args.logs.resolve()
    args.logs.mkdir(parents=True, exist_ok=True)
    resources = json.loads((args.package / "libexec/resources.json").read_text())
    spec = importlib.util.spec_from_file_location("officectl", args.package / "libexec/officectl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="office-download-check-", dir=args.logs) as directory:
        root = Path(directory)
        office = module.Office(resources, dict(
            os.environ, XDG_DATA_HOME=str(root / "data"), XDG_STATE_HOME=str(root / "state"),
            XDG_CACHE_HOME=str(root / "cache"),
        ))
        assert office.prefix.is_relative_to(root)
        env = office.environment()
        env.update(WINEDEBUG="-all,err+all,+timestamp,+pid,+tid,+seh,+qmgr,+loaddll",
                   WINEDLLOVERRIDES="winemenubuilder.exe=d;winedbg.exe=d")
        office.prefix.parent.mkdir(parents=True, exist_ok=True)
        try:
            with (args.logs / "wineboot.log").open("w") as log:
                office.run_wine(["wineboot", "-u"], env, log)
                subprocess.run([resources["wineserver"], "-w"], env=env,
                               stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)
            runner = Path(resources["wine"]).parent.parent
            for directory, arch in (("system32", "x86_64-windows"), ("syswow64", "i386-windows")):
                installed = office.prefix / "drive_c/windows" / directory / "qmgr.dll"
                built = runner / "lib/wine" / arch / "qmgr.dll"
                assert installed.read_bytes() == built.read_bytes(), installed
            for label, fail_range in (("normal", False), ("short-response", True)):
                server = ThreadingHTTPServer(("127.0.0.1", 0), partial(RangeHandler, fail_range=fail_range))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                path = args.logs / (label + ".log")
                try:
                    with path.open("w") as log:
                        result = subprocess.run([
                            resources["wine"], str(args.probe.resolve()),
                            f"http://127.0.0.1:{server.server_port}/ranges",
                            module.windows_path(str(root / "payload.bin")),
                        ], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120)
                    text = path.read_text(errors="replace")
                    expected = 1 if fail_range else 0
                    assert result.returncode == expected, (label, result.returncode, path)
                    assert text.count("ServiceAfterTransfer=00000000") == 3, path
                    marker = "JobError=80200013" if fail_range else "Complete=00000000"
                    assert text.count(marker) == 3, path
                    assert "Unhandled page fault" not in text and "code=c0000005" not in text, path
                    print(f"{label}: 3 jobs; service survived; passed", flush=True)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)
        finally:
            office.stop_server(env)


if __name__ == "__main__":
    main()
