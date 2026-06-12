import base64
import html
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="minimal-oidc-sharedpass")

ISSUER = os.environ.get("ISSUER", "").rstrip("/")
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "")
EMAIL_DOMAIN = os.environ.get("EMAIL_DOMAIN", "")
SHARED_PASSWORD = os.environ.get("SHARED_PASSWORD", "")
ALLOW_ANY_PREFIX = os.environ.get("ALLOW_ANY_PREFIX", "false").lower() == "true"
ALLOWED_PREFIXES = {
    x.strip().lower()
    for x in os.environ.get("ALLOWED_PREFIXES", "").split(",")
    if x.strip()
}
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "300"))

PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
KEY_PATH = DATA_DIR / "private_key.pem"
KID_PATH = DATA_DIR / "kid.txt"

codes: Dict[str, dict] = {}


def require_config() -> None:
    missing = []
    for key, value in {
        "ISSUER": ISSUER,
        "OIDC_CLIENT_ID": CLIENT_ID,
        "OIDC_CLIENT_SECRET": CLIENT_SECRET,
        "OIDC_REDIRECT_URI": REDIRECT_URI,
        "EMAIL_DOMAIN": EMAIL_DOMAIN,
        "SHARED_PASSWORD": SHARED_PASSWORD,
    }.items():
        if not value:
            missing.append(key)
    if missing:
        raise RuntimeError("Missing env vars: " + ", ".join(missing))


def load_or_create_key():
    """Load a stable signing key.

    Render filesystems can be ephemeral unless you attach a persistent disk.
    For production, set OIDC_PRIVATE_KEY_PEM or OIDC_PRIVATE_KEY_B64 so JWKS stays
    stable across deploys/restarts. If neither is set, we fall back to DATA_DIR.
    """
    key_pem = os.environ.get("OIDC_PRIVATE_KEY_PEM", "").strip()
    key_b64 = os.environ.get("OIDC_PRIVATE_KEY_B64", "").strip()

    if key_b64 and not key_pem:
        key_pem = base64.b64decode(key_b64).decode("utf-8")

    if key_pem:
        private_key = serialization.load_pem_private_key(key_pem.encode("utf-8"), password=None)
        kid = os.environ.get("OIDC_KEY_ID", "render-sso-key-1").strip() or "render-sso-key-1"
        return private_key, kid

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists():
        private_key = serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    else:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        KEY_PATH.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    if KID_PATH.exists():
        kid = KID_PATH.read_text().strip()
    else:
        kid = secrets.token_urlsafe(12)
        KID_PATH.write_text(kid)
    return private_key, kid


private_key, key_id = load_or_create_key()


def b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def public_jwk() -> dict:
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "kid": key_id,
        "alg": "RS256",
        "n": b64url_uint(numbers.n),
        "e": b64url_uint(numbers.e),
    }


def check_client(client_id: str, redirect_uri: str, response_type: str) -> None:
    if client_id != CLIENT_ID:
        raise HTTPException(status_code=400, detail="invalid client_id")
    if redirect_uri != REDIRECT_URI:
        raise HTTPException(status_code=400, detail="invalid redirect_uri")
    if response_type != "code":
        raise HTTPException(status_code=400, detail="unsupported response_type")


def prefix_to_email(prefix: str) -> str:
    p = prefix.strip().lower()
    if not PREFIX_RE.match(p):
        raise ValueError("prefix must match ^[a-z0-9][a-z0-9._-]{0,62}$")
    if not ALLOW_ANY_PREFIX and p not in ALLOWED_PREFIXES:
        raise ValueError("prefix is not allowed")
    return f"{p}@{EMAIL_DOMAIN}"


def html_page(query: dict, error: Optional[str] = None) -> str:
    hidden = "\n".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(str(v))}">' for k, v in query.items()
    )
    error_block = f'<p class="error">{html.escape(error)}</p>' if error else ""
    allow_text = "任意前缀已启用" if ALLOW_ANY_PREFIX else "仅允许 ALLOWED_PREFIXES 里的前缀"
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OIDC Login</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f7f9; }}
    main {{ max-width: 420px; margin: 12vh auto; background: white; padding: 28px; border-radius: 16px; box-shadow: 0 10px 30px rgba(0,0,0,.08); }}
    label {{ display: block; margin: 16px 0 6px; font-weight: 600; }}
    input {{ width: 100%; box-sizing: border-box; padding: 11px 12px; border: 1px solid #d0d5dd; border-radius: 10px; font-size: 16px; }}
    button {{ margin-top: 18px; width: 100%; padding: 12px; border: 0; border-radius: 10px; font-size: 16px; font-weight: 700; cursor: pointer; }}
    .hint {{ color: #667085; font-size: 13px; line-height: 1.5; }}
    .error {{ color: #b42318; background: #fee4e2; padding: 10px; border-radius: 10px; }}
  </style>
</head>
<body>
<main>
  <h1>SSO Login</h1>
  {error_block}
  <p class="hint">输入前缀后会签发 <code>prefix@{html.escape(EMAIL_DOMAIN)}</code>。当前模式：{html.escape(allow_text)}。</p>
  <form method="post" action="/authorize">
    {hidden}
    <label for="prefix">prefix</label>
    <input id="prefix" name="prefix" autocomplete="username" required autofocus>
    <label for="password">password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Continue</button>
  </form>
</main>
</body>
</html>
"""


@app.on_event("startup")
def startup_check():
    require_config()


@app.get("/.well-known/openid-configuration")
def discovery():
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "profile"],
        "claims_supported": ["sub", "email", "email_verified", "name", "given_name", "family_name"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
    }


@app.get("/jwks.json")
def jwks():
    return {"keys": [public_jwk()]}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def root():
    return {
        "ok": True,
        "service": "minimal-oidc-sharedpass",
        "discovery": f"{ISSUER}/.well-known/openid-configuration" if ISSUER else "/.well-known/openid-configuration",
    }


@app.get("/authorize", response_class=HTMLResponse)
def authorize_get(
    client_id: str,
    redirect_uri: str,
    response_type: str,
    scope: str = "openid email profile",
    state: str = "",
    nonce: str = "",
):
    check_client(client_id, redirect_uri, response_type)
    return html_page(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": response_type,
            "scope": scope,
            "state": state,
            "nonce": nonce,
        }
    )


@app.post("/authorize", response_class=HTMLResponse)
def authorize_post(
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    response_type: str = Form(...),
    scope: str = Form("openid email profile"),
    state: str = Form(""),
    nonce: str = Form(""),
    prefix: str = Form(...),
    password: str = Form(...),
):
    check_client(client_id, redirect_uri, response_type)
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": response_type,
        "scope": scope,
        "state": state,
        "nonce": nonce,
    }
    if not secrets.compare_digest(password, SHARED_PASSWORD):
        return html_page(query, "invalid password")
    try:
        email = prefix_to_email(prefix)
    except ValueError as exc:
        return html_page(query, str(exc))

    code = secrets.token_urlsafe(32)
    codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "nonce": nonce,
        "email": email,
        "prefix": prefix.strip().lower(),
        "iat": int(time.time()),
    }
    params = {"code": code}
    if state:
        params["state"] = state
    return RedirectResponse(f"{redirect_uri}?{urlencode(params)}", status_code=303)


def get_client_auth(request: Request, form: dict) -> tuple[str, str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        raw = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
        cid, secret = raw.split(":", 1)
        return cid, secret
    return form.get("client_id", ""), form.get("client_secret", "")


@app.post("/token")
async def token(request: Request):
    form_data = await request.form()
    form = dict(form_data)
    client_id, client_secret = get_client_auth(request, form)
    if client_id != CLIENT_ID or not secrets.compare_digest(client_secret, CLIENT_SECRET):
        raise HTTPException(status_code=401, detail="invalid client authentication")
    if form.get("grant_type") != "authorization_code":
        raise HTTPException(status_code=400, detail="unsupported grant_type")
    code = form.get("code", "")
    record = codes.pop(code, None)
    if not record:
        raise HTTPException(status_code=400, detail="invalid code")
    if int(time.time()) - record["iat"] > TOKEN_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="expired code")
    if form.get("redirect_uri") != record["redirect_uri"]:
        raise HTTPException(status_code=400, detail="redirect_uri mismatch")

    now = int(time.time())
    email = record["email"]
    claims = {
        "iss": ISSUER,
        "sub": email,
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + 3600,
        "auth_time": record["iat"],
        "email": email,
        "email_verified": True,
        "name": email,
        "given_name": record["prefix"],
        "family_name": "SSO",
    }
    if record.get("nonce"):
        claims["nonce"] = record["nonce"]

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    id_token = jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": key_id})
    return JSONResponse(
        {
            "access_token": secrets.token_urlsafe(32),
            "token_type": "Bearer",
            "expires_in": 3600,
            "id_token": id_token,
        }
    )
