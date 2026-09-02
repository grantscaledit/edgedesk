#!/usr/bin/env python3
"""Run the Edge Desk web app locally.

    python scripts/serve.py            # http://127.0.0.1:5000
    python scripts/serve.py --port 8080

Bound to localhost by design. There is no authentication, the pages expose
a live database connection, and the decision form writes rows -- none of
which should be reachable from anything but this machine.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk.web.app import create_app                          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    print(f"\n  Edge Desk  ->  http://127.0.0.1:{a.port}\n")
    create_app().run(host="127.0.0.1", port=a.port, debug=a.debug)


if __name__ == "__main__":
    main()
