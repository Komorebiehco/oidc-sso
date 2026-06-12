import base64
import html
import os
import re
import secrets
import time
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="render-chatgpt-team-oidc-sso")

ISSUER = (os.environ.get("ISSUER") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "")
EMAIL_DOMAIN = os.environ.get("EMAIL_DOMAIN", "").lower().strip()
EMAIL_DOMAINS = [domain.strip() for domain in EMAIL_DOMAIN.split(",") if domain.strip()]
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
        "EMAIL_DOMAIN": ",".join(EMAIL_DOMAINS),
        "SHARED_PASSWORD": SHARED_PASSWORD,
    }.items():
        if not value:
            missing.append(key)
    if missing:
        raise RuntimeError("Missing env vars: " + ", ".join(missing))


def load_or_create_key():
    """Load a stable RSA signing key for ID tokens and JWKS."""
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


def prefix_to_email(prefix: str, domain: str) -> str:
    normalized = prefix.strip().lower()
    selected_domain = domain.strip().lower()
    if not PREFIX_RE.match(normalized):
        raise ValueError("Email prefix must start with a letter or number and may only contain letters, numbers, dots, underscores, or hyphens.")
    if selected_domain not in EMAIL_DOMAINS:
        raise ValueError("This email domain is not allowed.")
    if not ALLOW_ANY_PREFIX and normalized not in ALLOWED_PREFIXES:
        raise ValueError("This email prefix is not allowed.")
    return f"{normalized}@{selected_domain}"


def redirect_with_params(url: str, params: dict) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def html_page(query: dict, error: Optional[str] = None) -> str:
    hidden = "\n".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(str(v))}">'
        for k, v in query.items()
    )
    error_block = f'<p class="error">{html.escape(error)}</p>' if error else ""
    allow_text = "any email prefix is allowed" if ALLOW_ANY_PREFIX else "only configured email prefixes are allowed"
    domains = EMAIL_DOMAINS or [EMAIL_DOMAIN]
    domain_options = "\n".join(
        f'<option value="{html.escape(domain)}">{html.escape(domain)}</option>'
        for domain in domains
    )
    domain_hint = ", ".join(domains)
    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ChatGPT Team SSO</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f7f9; color: #111827; }}
    main {{ width: min(420px, calc(100vw - 32px)); margin: 12vh auto; background: white; padding: 28px; border: 1px solid #e5e7eb; border-radius: 8px; box-shadow: 0 14px 36px rgba(17,24,39,.08); }}
    h1 {{ margin: 0 0 10px; font-size: 24px; line-height: 1.2; }}
    p {{ margin: 10px 0; }}
    label {{ display: block; margin: 16px 0 6px; font-weight: 600; }}
    input, select {{ width: 100%; padding: 11px 12px; border: 1px solid #d0d5dd; border-radius: 8px; font-size: 16px; background: #fff; }}
    button {{ margin-top: 18px; width: 100%; padding: 12px; border: 0; border-radius: 8px; background: #111827; color: #fff; font-size: 16px; font-weight: 700; cursor: pointer; }}
    .hint {{ color: #667085; font-size: 13px; line-height: 1.5; }}
    .error {{ color: #b42318; background: #fee4e2; padding: 10px; border-radius: 8px; }}
    code {{ overflow-wrap: anywhere; }}
  </style>
</head>
<body>
<main>
  <h1>Sign in to ChatGPT Team</h1>
  {error_block}
  <p class="hint">Enter the email prefix and choose one allowed domain. Current mode: {html.escape(allow_text)}.</p>
  <form method="post" action="/authorize">
    {hidden}
    <label for="prefix">Email prefix</label>
    <input id="prefix" name="prefix" autocomplete="username" required autofocus>
    <label for="domain">Email domain</label>
    <select id="domain" name="domain" required>
      {domain_options}
    </select>
    <label for="password">Shared password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Continue to ChatGPT</button>
  </form>
  <p class="hint">Allowed domains: <code>{html.escape(domain_hint)}</code></p>
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
        "grant_types_supported": ["authorization_code"],
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
    discovery_url = f"{ISSUER}/.well-known/openid-configuration" if ISSUER else "/.well-known/openid-configuration"
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>ChatGPT Team OIDC SSO</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:760px;margin:48px auto;padding:0 20px;line-height:1.55;color:#111827}}code,pre{{background:#f5f5f5;border-radius:6px;padding:2px 6px}}pre{{padding:14px;overflow:auto}}</style></head>
<body><h1>ChatGPT Team OIDC SSO</h1><p>This service is running. Configure ChatGPT Team with this discovery URL:</p><pre>{html.escape(discovery_url)}</pre><p>Health check: <code>/healthz</code></p></body></html>"""
    )


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
    domain: str = Form(...),
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
        return html_page(query, "Invalid shared password.")
    try:
        email = prefix_to_email(prefix, domain)
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
    return RedirectResponse(redirect_with_params(redirect_uri, params), status_code=303)


def get_client_auth(request: Request, form: dict) -> tuple[str, str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            raw = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
            cid, secret = raw.split(":", 1)
        except Exception:
            raise HTTPException(status_code=401, detail="invalid client authentication")
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
    if client_id != record["client_id"]:
        raise HTTPException(status_code=400, detail="client_id mismatch")
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
