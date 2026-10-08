from __future__ import annotations

import socket
import sys
import os
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
ALEMBIC_DIR = ROOT / "alembic"
HOST = "127.0.0.1"
PORT = 8000

# Always import this checkout, even when launched from another directory.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def _port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


if __name__ == "__main__":
    if _port_is_open(HOST, PORT):
        print(
            f"Backend already appears to be running on http://{HOST}:{PORT}. "
            "Stop the existing process before starting another one.",
            file=sys.stderr,
        )
        sys.exit(0)

    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=False,
    )
