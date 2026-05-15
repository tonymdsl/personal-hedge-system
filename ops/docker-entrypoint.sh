#!/bin/sh
set -eu

mkdir -p .streamlit cache output

if [ -n "${STREAMLIT_AUTH_SECRETS_TOML:-}" ]; then
  printf '%s\n' "$STREAMLIT_AUTH_SECRETS_TOML" > .streamlit/secrets.toml
  chmod 600 .streamlit/secrets.toml
elif [ -n "${STREAMLIT_AUTH_REDIRECT_URI:-}" ] || [ -n "${CLERK_OAUTH_CLIENT_ID:-}" ] || [ -n "${STREAMLIT_AUTH_CLIENT_ID:-}" ]; then
  python - <<'PY'
from __future__ import annotations

import os
import secrets
from pathlib import Path


def first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return ""


def toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


values = {
    "redirect_uri": first_env("STREAMLIT_AUTH_REDIRECT_URI"),
    "cookie_secret": first_env("STREAMLIT_AUTH_COOKIE_SECRET") or secrets.token_hex(32),
    "client_id": first_env("STREAMLIT_AUTH_CLIENT_ID", "CLERK_OAUTH_CLIENT_ID"),
    "client_secret": first_env("STREAMLIT_AUTH_CLIENT_SECRET", "CLERK_OAUTH_CLIENT_SECRET"),
    "server_metadata_url": first_env(
        "STREAMLIT_AUTH_SERVER_METADATA_URL",
        "CLERK_OIDC_METADATA_URL",
        "CLERK_OAUTH_METADATA_URL",
    ),
}
missing = [key for key, value in values.items() if not value]
if missing:
    raise SystemExit("Missing Streamlit auth environment variables: " + ", ".join(missing))

prompt = first_env("STREAMLIT_AUTH_PROMPT") or "consent"
content = ["[auth]"]
for key, value in values.items():
    content.append(f"{key} = {toml_string(value)}")
content.append(f'client_kwargs = {{ "prompt" = {toml_string(prompt)} }}')
Path(".streamlit").mkdir(exist_ok=True)
Path(".streamlit/secrets.toml").write_text("\n".join(content) + "\n", encoding="utf-8")
Path(".streamlit/secrets.toml").chmod(0o600)
PY
fi

exec "$@"
