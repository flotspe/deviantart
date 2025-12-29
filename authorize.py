#!/usr/bin/env python3
"""
DeviantArt OAuth: obtain a REFRESH TOKEN (one-time).

What it does:
1) Starts a temporary local HTTP server on http://127.0.0.1:<PORT>/callback
2) Opens your browser to DeviantArt's authorize URL
3) Captures the returned ?code=...
4) Exchanges that code for access_token + refresh_token
5) Prints the refresh_token (store it securely)

Prereqs:
  pip install requests

Before running, set env vars:
  DA_CLIENT_ID
  DA_CLIENT_SECRET
Optional:
  DA_SCOPES (default: "browse gallery")
  DA_PORT   (default: 8123)

Run:
  python get_da_refresh_token.py
"""

import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv
from token_store import save_refresh_token

AUTHORIZE_URL = "https://www.deviantart.com/oauth2/authorize"
TOKEN_URL = "https://www.deviantart.com/oauth2/token"


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    # Shared state (set by handler, read by main)
    auth_code: str | None = None
    auth_error: str | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        qs = parse_qs(parsed.query)
        if "error" in qs:
            self.__class__.auth_error = qs.get("error", ["unknown_error"])[0]
        if "code" in qs:
            self.__class__.auth_code = qs.get("code", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        if self.__class__.auth_code:
            self.wfile.write(
                b"<h2>Authorization received.</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
            )
        else:
            self.wfile.write(
                b"<h2>No authorization code received.</h2>"
                b"<p>Check the terminal for details.</p>"
            )


def build_authorize_url(client_id: str, redirect_uri: str, scopes: str) -> str:
    # DeviantArt expects space-separated scopes in the authorize URL.
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_tokens(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    timeout_s: int = 30,
) -> dict:
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    resp = requests.post(TOKEN_URL, data=data, timeout=timeout_s)
    resp.raise_for_status()
    return resp.json()


def get_refresh_token() -> str | None:
    load_dotenv()
    client_id = os.environ.get("DA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DA_CLIENT_SECRET", "").strip()
    scopes = os.environ.get("DA_SCOPES", "browse gallery").strip()
    port = int(os.environ.get("DA_PORT", "8123"))

    if not client_id or not client_secret:
        print(
            "Missing env vars.\n"
            "Set:\n"
            "  DA_CLIENT_ID\n"
            "  DA_CLIENT_SECRET\n"
            "Optional:\n"
            "  DA_SCOPES (default: 'browse gallery')\n"
            "  DA_PORT   (default: 8123)\n",
            file=sys.stderr,
        )
        raise RuntimeError("Required environment variables not set")

    redirect_uri = f"http://127.0.0.1:{port}/callback"

    # Start local callback server
    server = HTTPServer(("127.0.0.1", port), OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    auth_url = build_authorize_url(client_id, redirect_uri, scopes)

    print("Opening browser for DeviantArt authorization...")
    print(f"Redirect URI: {redirect_uri}")
    print(f"Scopes:       {scopes}")
    print()
    print("If your browser does not open automatically, copy/paste this URL:")
    print(auth_url)
    print()

    webbrowser.open(auth_url, new=1, autoraise=True)

    # Wait for callback
    deadline = time.time() + 300  # 5 minutes
    while time.time() < deadline:
        if OAuthCallbackHandler.auth_error:
            print(f"Authorization error: {OAuthCallbackHandler.auth_error}", file=sys.stderr)
            server.shutdown()
            return 1
        if OAuthCallbackHandler.auth_code:
            break
        time.sleep(0.25)

    server.shutdown()

    code = OAuthCallbackHandler.auth_code
    if not code:
        print("Timed out waiting for the authorization callback.", file=sys.stderr)
        print("Try again, or verify your app's Redirect URI matches exactly.", file=sys.stderr)
        return None

    print("Exchanging authorization code for tokens...")
    tokens = exchange_code_for_tokens(client_id, client_secret, redirect_uri, code)

    refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token")
    expires_in = tokens.get("expires_in")
    scope_returned = tokens.get("scope")

    if not refresh_token:
        print("Token response did not include refresh_token. Full response:", file=sys.stderr)
        print(json.dumps(tokens, indent=2), file=sys.stderr)
        return None

    print()
    print("SUCCESS. Store this refresh token securely:")
    print(refresh_token)
    print()
    print("Additional info (for debugging):")
    print(f"  access_token present: {'yes' if access_token else 'no'}")
    print(f"  expires_in:           {expires_in}")
    print(f"  scope returned:       {scope_returned}")
    return refresh_token
