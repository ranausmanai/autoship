#!/usr/bin/env python3
"""Issue a one-time autoship invite code."""

import json
import secrets
import sys
import time
from pathlib import Path


INVITES_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/opt/autoship/api/invites.json")
label = sys.argv[2] if len(sys.argv) > 2 else ""


def load():
    if not INVITES_PATH.exists():
        return {}
    try:
        data = json.loads(INVITES_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def main():
    invites = load()
    code = f"SHIP-{secrets.token_hex(3).upper()}"
    invites[code] = {
        "label": label,
        "created_at": int(time.time()),
    }
    INVITES_PATH.parent.mkdir(parents=True, exist_ok=True)
    INVITES_PATH.write_text(json.dumps(invites, indent=2) + "\n")
    print(code)


if __name__ == "__main__":
    main()
