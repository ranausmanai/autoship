#!/usr/bin/env python3
"""Minimal private deploy API for autoship.fun.

This service is meant to run on the autoship.fun host behind nginx.
It accepts a tar.gz bundle from the open-source CLI and deploys it by
calling deploy_release.sh with local infrastructure credentials kept in env.
"""

import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DEPLOY_ROOT = os.getenv("AUTOSHIP_DEPLOY_ROOT", "/opt/autoship")
DOMAIN = os.getenv("AUTOSHIP_DOMAIN", "autoship.fun")
EMAIL = os.getenv("AUTOSHIP_EMAIL", "")
TOKEN = os.getenv("AUTOSHIP_DEPLOY_TOKEN", "")
SCRIPT = Path(os.getenv("AUTOSHIP_DEPLOY_SCRIPT", Path(__file__).with_name("deploy_release.sh")))
MAX_BYTES = int(os.getenv("AUTOSHIP_MAX_BYTES", str(25 * 1024 * 1024)))
BIND = os.getenv("AUTOSHIP_BIND", "127.0.0.1")
PORT = int(os.getenv("AUTOSHIP_PORT", "9100"))
ALLOW_CUSTOM_DOMAINS = os.getenv("AUTOSHIP_ALLOW_CUSTOM_DOMAINS") == "1"
PUBLIC_BETA = os.getenv("AUTOSHIP_PUBLIC_BETA") == "1"
TOKENS_PATH = Path(os.getenv("AUTOSHIP_TOKENS_PATH", "/opt/autoship/api/tokens.json"))
INVITES_PATH = Path(os.getenv("AUTOSHIP_INVITES_PATH", "/opt/autoship/api/invites.json"))
PAIRINGS_PATH = Path(os.getenv("AUTOSHIP_PAIRINGS_PATH", "/opt/autoship/api/pairings.json"))


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (slug or "app")[:48]


def json_bytes(payload):
    return (json.dumps(payload) + "\n").encode()


def load_json_file(path, default):
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return default
    return data


def write_json_file(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)


def load_tokens():
    tokens = {}
    if TOKEN:
        tokens[TOKEN] = {"source": "env", "created_at": 0}
    data = load_json_file(TOKENS_PATH, {})
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and isinstance(value, dict):
                tokens[key] = value
    return tokens


def load_invites():
    data = load_json_file(INVITES_PATH, {})
    return data if isinstance(data, dict) else {}


def load_pairings():
    data = load_json_file(PAIRINGS_PATH, {})
    return data if isinstance(data, dict) else {}


def create_token(meta):
    token = secrets.token_urlsafe(24)
    tokens = load_json_file(TOKENS_PATH, {})
    if not isinstance(tokens, dict):
        tokens = {}
    record = {"created_at": int(time.time())}
    record.update(meta)
    tokens[token] = record
    write_json_file(TOKENS_PATH, tokens)
    return token


def consume_invite(code):
    code = (code or "").strip().upper()
    invites = load_invites()
    meta = invites.get(code)
    if not isinstance(meta, dict):
        return None
    invites.pop(code, None)
    write_json_file(INVITES_PATH, invites)

    return create_token(
        {
        "invite_code": code,
        "label": str(meta.get("label", "")),
        "source": "invite",
        }
    )


def prune_pairings(pairings):
    now = int(time.time())
    return {
        key: value
        for key, value in pairings.items()
        if isinstance(value, dict) and int(value.get("expires_at", now + 1)) > now
    }


def create_pairing():
    pairings = prune_pairings(load_pairings())
    session = secrets.token_urlsafe(18)
    code = f"{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
    pairings[session] = {
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + 600,
        "code": code,
        "status": "pending",
    }
    write_json_file(PAIRINGS_PATH, pairings)
    return session, code


def approve_pairing(session):
    pairings = prune_pairings(load_pairings())
    record = pairings.get(session)
    if not isinstance(record, dict):
        return None
    token = record.get("token") or create_token({"source": "browser_pair", "session": session})
    record["status"] = "approved"
    record["token"] = token
    record["approved_at"] = int(time.time())
    pairings[session] = record
    write_json_file(PAIRINGS_PATH, pairings)
    return token


def pairing_status(session):
    pairings = prune_pairings(load_pairings())
    write_json_file(PAIRINGS_PATH, pairings)
    record = pairings.get(session)
    if not isinstance(record, dict):
        return None
    return record


class Handler(BaseHTTPRequestHandler):
    server_version = "autoship-api/0.1"

    def log_message(self, fmt, *args):
        return

    def cors_origin(self):
        origin = self.headers.get("Origin", "")
        allowed = {
            f"https://{DOMAIN}",
            f"http://{DOMAIN}",
            f"https://www.{DOMAIN}",
            f"http://www.{DOMAIN}",
        }
        return origin if origin in allowed else ""

    def send_json(self, status, payload, *, cors=False):
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cors:
            origin = self.cors_origin()
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if self.path != "/authorize/complete":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        origin = self.cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Vary", "Origin")
        self.end_headers()

    def authorized(self):
        tokens = load_tokens()
        if PUBLIC_BETA:
            return True
        if not tokens:
            return False
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        token = header.removeprefix("Bearer ").strip()
        return token in tokens

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/authorize/poll":
            self.handle_poll(parsed)
            return
        if parsed.path != "/health":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self.send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "domain": DOMAIN,
                "deploy_root": DEPLOY_ROOT,
                "script": str(SCRIPT),
                "public_beta": PUBLIC_BETA,
            },
        )

    def do_POST(self):
        if self.path == "/authorize/start":
            self.handle_authorize_start()
            return
        if self.path == "/authorize/complete":
            self.handle_authorize_complete()
            return
        if self.path == "/claim":
            self.handle_claim()
            return
        if self.path != "/deploy":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self.authorized():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        if not SCRIPT.exists():
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "missing_deploy_script"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "empty_body"})
            return
        if length > MAX_BYTES:
            self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "bundle_too_large", "max_bytes": MAX_BYTES})
            return

        slug = slugify(self.headers.get("X-Autoship-Slug", ""))
        requested_domain = self.headers.get("X-Autoship-Domain", DOMAIN) or DOMAIN
        domain = requested_domain if ALLOW_CUSTOM_DOMAINS else DOMAIN
        email = self.headers.get("X-Autoship-Email", EMAIL) or EMAIL
        bundle = self.rfile.read(length)

        with tempfile.NamedTemporaryFile(prefix="autoship-api-", suffix=".tgz", delete=False) as fh:
            fh.write(bundle)
            archive_path = fh.name

        try:
            result = subprocess.run(
                ["bash", str(SCRIPT), slug, domain, email or "__none__", archive_path, DEPLOY_ROOT],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or exc.stdout or "").strip()
            self.send_json(
                HTTPStatus.BAD_GATEWAY,
                {
                    "error": "deploy_failed",
                    "slug": slug,
                    "details": details[-4000:],
                },
            )
            return
        finally:
            Path(archive_path).unlink(missing_ok=True)

        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            self.send_json(HTTPStatus.BAD_GATEWAY, {"error": "empty_deploy_result", "slug": slug})
            return

        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError:
            self.send_json(
                HTTPStatus.BAD_GATEWAY,
                {
                    "error": "bad_deploy_result",
                    "slug": slug,
                    "details": result.stdout[-4000:],
                },
            )
            return

        payload["slug"] = slug
        self.send_json(HTTPStatus.OK, payload)

    def handle_claim(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 4096:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode())
        except json.JSONDecodeError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return

        code = str(payload.get("code", "")).strip()
        if not code:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "missing_code"})
            return

        token = consume_invite(code)
        if not token:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid_code"})
            return

        self.send_json(
            HTTPStatus.OK,
            {
                "token": token,
                "api_url": f"https://api.{DOMAIN}/deploy",
                "domain": DOMAIN,
            },
        )

    def handle_authorize_start(self):
        session, code = create_pairing()
        self.send_json(
            HTTPStatus.OK,
            {
                "session": session,
                "code": code,
                "approve_url": f"https://{DOMAIN}/connect.html?session={session}&code={code}",
                "poll_url": f"https://api.{DOMAIN}/authorize/poll?session={session}",
                "expires_in": 600,
            },
        )

    def handle_poll(self, parsed):
        session = parse_qs(parsed.query).get("session", [""])[0].strip()
        if not session:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "missing_session"})
            return
        record = pairing_status(session)
        if not record:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "invalid_session"})
            return
        if record.get("status") != "approved" or not record.get("token"):
            self.send_json(HTTPStatus.ACCEPTED, {"status": "pending"})
            return
        self.send_json(
            HTTPStatus.OK,
            {
                "status": "approved",
                "token": record["token"],
                "api_url": f"https://api.{DOMAIN}/deploy",
                "domain": DOMAIN,
            },
        )

    def handle_authorize_complete(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 4096:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"}, cors=True)
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode())
        except json.JSONDecodeError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"}, cors=True)
            return

        session = str(payload.get("session", "")).strip()
        if not session:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "missing_session"}, cors=True)
            return

        token = approve_pairing(session)
        if not token:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "invalid_session"}, cors=True)
            return

        self.send_json(HTTPStatus.OK, {"ok": True}, cors=True)


def main():
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"autoship api listening on http://{BIND}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
