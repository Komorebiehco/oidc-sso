import base64
import hashlib
import hmac
import html
import json
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
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="render-chatgpt-team-oidc-sso")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

ISSUER = (os.environ.get("ISSUER") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "")
EMAIL_DOMAIN = os.environ.get("EMAIL_DOMAIN", "").lower().strip()
EMAIL_DOMAINS = [domain.strip() for domain in EMAIL_DOMAIN.split(",") if domain.strip()]
SHARED_PASSWORD = os.environ.get("SHARED_PASSWORD", "")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", SHARED_PASSWORD)
APP_SECRET = os.environ.get("APP_SECRET") or CLIENT_SECRET or SHARED_PASSWORD or "dev-secret-change-me"
ALLOW_ANY_PREFIX = os.environ.get("ALLOW_ANY_PREFIX", "false").lower() == "true"
ALLOWED_PREFIXES = {
    x.strip().lower()
    for x in os.environ.get("ALLOWED_PREFIXES", "").split(",")
    if x.strip()
}
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "300"))
DEFAULT_BACKGROUND_URL = "/static/background.png"
LOGIN_BACKGROUND_URL = os.environ.get("LOGIN_BACKGROUND_URL", DEFAULT_BACKGROUND_URL).strip() or DEFAULT_BACKGROUND_URL
SERVICE_NAME = os.environ.get("SERVICE_NAME", "Komorebi SSO").strip() or "Komorebi SSO"

PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
KEY_PATH = DATA_DIR / "private_key.pem"
KID_PATH = DATA_DIR / "kid.txt"

codes: Dict[str, dict] = {}
profiles: Dict[str, dict] = {}
invitations: Dict[str, dict] = {}
app_settings: Dict[str, object] = {"invite_required": True}


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


def safe_local_redirect(value: str) -> str:
    target = (value or "/console").strip()
    if not target.startswith("/") or target.startswith("//"):
        return "/console"
    return target


def now_ts() -> int:
    return int(time.time())


def data_file(name: str) -> Path:
    return DATA_DIR / name


def load_json_file(name: str, default):
    path = data_file(name)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json_file(name: str, value) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data_file(name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load_app_state() -> None:
    profiles.update(load_json_file("profiles.json", {}))
    invitations.update(load_json_file("invitations.json", {}))
    app_settings.update(load_json_file("settings.json", {}))


def save_profiles() -> None:
    save_json_file("profiles.json", profiles)


def save_invitations() -> None:
    save_json_file("invitations.json", invitations)


def save_settings() -> None:
    save_json_file("settings.json", app_settings)


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 180_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        method, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if method != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 180_000).hex()
    return secrets.compare_digest(candidate, digest)


def clean_invite_code(value: str) -> str:
    return re.sub(r"[^A-Z0-9-]", "", str(value or "").strip().upper())


def make_invite_code() -> str:
    return f"INV-{secrets.token_urlsafe(8).replace('_', '').replace('-', '').upper()[:10]}"


def invite_available(code: str) -> tuple[bool, str]:
    invite = invitations.get(clean_invite_code(code))
    if not invite:
        return False, "邀请码不存在。"
    if not invite.get("active", True):
        return False, "邀请码已停用。"
    expires_at = int(invite.get("expires_at") or 0)
    if expires_at and expires_at < now_ts():
        return False, "邀请码已过期。"
    max_uses = int(invite.get("max_uses") or 1)
    uses = int(invite.get("uses") or 0)
    if max_uses > 0 and uses >= max_uses:
        return False, "邀请码已使用完。"
    return True, ""


def consume_invite(code: str, email: str) -> None:
    key = clean_invite_code(code)
    invite = invitations[key]
    invite["uses"] = int(invite.get("uses") or 0) + 1
    invite.setdefault("used_by", []).append({"email": email, "used_at": now_ts()})
    save_invitations()


def authenticate_or_register(
    *,
    mode: str,
    prefix: str,
    domain: str,
    password: str,
    display_name: str = "",
    invite_code: str = "",
) -> tuple[str, dict]:
    email = prefix_to_email(prefix, domain)
    normalized_prefix = prefix.strip().lower()
    existing = profiles.get(email)
    if mode == "register":
        if existing and existing.get("password_hash"):
            raise ValueError("这个账号已经注册，请直接登录。")
        if not password:
            raise ValueError("请设置账号密码。")
        ok, reason = invite_available(invite_code)
        if not ok:
            raise ValueError(reason)
        profile = {
            "name": display_name.strip() or email,
            "prefix": normalized_prefix,
            "email": email,
            "password_hash": password_hash(password),
            "registered_at": now_ts(),
            "last_login_at": now_ts(),
        }
        profiles[email] = profile
        save_profiles()
        consume_invite(invite_code, email)
        return email, profile

    if not existing or not existing.get("password_hash"):
        raise ValueError("账号不存在，请先使用邀请码注册。")
    if not verify_password(password, existing["password_hash"]):
        raise ValueError("账号或密码错误。")
    existing["last_login_at"] = now_ts()
    save_profiles()
    return email, existing


def make_admin_token() -> str:
    expires = now_ts() + 12 * 60 * 60
    payload = f"{ADMIN_USERNAME}:{expires}"
    signature = hmac.new(APP_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode("utf-8")).decode("ascii")


def admin_token_valid(token: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, expires, signature = raw.rsplit(":", 2)
    except Exception:
        return False
    if username != ADMIN_USERNAME or int(expires) < now_ts():
        return False
    payload = f"{username}:{expires}"
    expected = hmac.new(APP_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return secrets.compare_digest(signature, expected)


def is_admin_request(request: Request) -> bool:
    return admin_token_valid(request.cookies.get("admin_auth", ""))


def fmt_time(ts: int | str | None) -> str:
    try:
        value = int(ts or 0)
    except (TypeError, ValueError):
        value = 0
    if not value:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))


def invite_available(code: str) -> tuple[bool, str]:
    invite = invitations.get(clean_invite_code(code))
    if not invite:
        return False, "邀请码不存在。"
    if not invite.get("active", True):
        return False, "邀请码已停用。"
    expires_at = int(invite.get("expires_at") or 0)
    if expires_at and expires_at < now_ts():
        return False, "邀请码已过期。"
    max_uses = int(invite.get("max_uses") or 1)
    uses = int(invite.get("uses") or 0)
    if max_uses > 0 and uses >= max_uses:
        return False, "邀请码已使用完。"
    return True, ""


def authenticate_or_register(
    *,
    mode: str,
    prefix: str,
    domain: str,
    password: str,
    display_name: str = "",
    invite_code: str = "",
) -> tuple[str, dict]:
    email = prefix_to_email(prefix, domain)
    normalized_prefix = prefix.strip().lower()
    existing = profiles.get(email)

    if mode == "register":
        if existing and existing.get("password_hash"):
            raise ValueError("这个账号已经注册，请直接登录。")
        if not password:
            raise ValueError("请设置账号密码。")

        invite_code = clean_invite_code(invite_code)
        invite_required = bool(app_settings.get("invite_required", True))
        if invite_required or invite_code:
            ok, reason = invite_available(invite_code)
            if not ok:
                raise ValueError(reason)

        profile = {
            "name": display_name.strip() or email,
            "prefix": normalized_prefix,
            "email": email,
            "password_hash": password_hash(password),
            "registered_at": now_ts(),
            "last_login_at": now_ts(),
        }
        profiles[email] = profile
        save_profiles()
        if invite_code:
            consume_invite(invite_code, email)
        return email, profile

    if not existing or not existing.get("password_hash"):
        raise ValueError("账号不存在，请先注册。")
    if not verify_password(password, existing["password_hash"]):
        raise ValueError("账号或密码错误。")
    existing["last_login_at"] = now_ts()
    save_profiles()
    return email, existing


load_app_state()


def root_page() -> str:
    discovery_url = f"{ISSUER}/.well-known/openid-configuration" if ISSUER else "/.well-known/openid-configuration"
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(SERVICE_NAME)} | OIDC SSO</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: #202939;
      background: #f6f7f9;
    }}
    a {{ color: inherit; text-decoration: none; }}
    .shell {{ width: min(1040px, calc(100% - 36px)); margin: 0 auto; }}
    .nav {{
      position: sticky;
      top: 0;
      z-index: 4;
      background: rgba(246, 247, 249, .84);
      border-bottom: 1px solid rgba(214, 219, 226, .72);
      backdrop-filter: blur(16px);
    }}
    .nav-inner {{
      min-height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
    }}
    .brand {{ display: inline-flex; align-items: center; gap: 10px; font-weight: 800; }}
    .mark {{
      display: grid;
      place-items: center;
      width: 32px;
      height: 32px;
      color: #fff;
      background: #24272f;
      border-radius: 8px;
      font-size: 12px;
    }}
    .nav-links {{ display: flex; align-items: center; gap: 30px; color: #717987; font-weight: 700; font-size: 14px; }}
    .nav-actions {{ display: flex; align-items: center; gap: 10px; }}
    .lang {{
      min-height: 36px;
      padding: 7px 10px;
      color: #394456;
      background: #fff;
      border: 1px solid #d8dde5;
      border-radius: 8px;
      font: inherit;
      font-size: 13px;
    }}
    .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 0 18px;
      border-radius: 8px;
      border: 1px solid #24272f;
      font-weight: 800;
      font-size: 14px;
    }}
    .button.primary {{ color: #fff; background: #24272f; box-shadow: 0 12px 28px rgba(27, 34, 47, .16); }}
    .button.secondary {{ color: #24272f; background: #fff; border-color: #d8dde5; }}
    .hero {{
      min-height: 560px;
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(320px, .95fr);
      align-items: center;
      gap: 52px;
      padding: 72px 0 68px;
    }}
    .eyebrow {{ margin: 0 0 14px; color: #7c8491; font-size: 14px; font-weight: 800; }}
    h1 {{ margin: 0; font-size: 46px; line-height: 1.15; letter-spacing: 0; }}
    .lead {{ max-width: 540px; margin: 22px 0 30px; color: #687385; font-size: 17px; line-height: 1.75; font-weight: 650; }}
    .hero-actions {{ display: flex; flex-wrap: wrap; gap: 12px; }}
    .art {{
      min-height: 330px;
      position: relative;
      display: grid;
      place-items: center;
    }}
    .shape {{
      position: absolute;
      border: 1px solid #e0e4ea;
      background: rgba(255,255,255,.36);
    }}
    .shape.one {{ width: 150px; height: 140px; top: 46px; left: 44px; border-radius: 8px; }}
    .shape.two {{ width: 96px; height: 96px; right: 76px; top: 148px; border-radius: 999px; }}
    .shape.three {{ width: 62px; height: 62px; right: 98px; top: 42px; border-radius: 8px; border-style: dashed; }}
    .line {{ position: absolute; width: 1px; height: 88px; background: #e4e7ec; transform: rotate(18deg); }}
    .line.a {{ left: 176px; bottom: 64px; }}
    .line.b {{ right: 170px; top: 116px; transform: rotate(32deg); }}
    section {{ border-top: 1px dashed #dfe3e8; padding: 70px 0; }}
    .section-title {{ width: min(760px, 100%); margin: 0 auto 42px; text-align: left; }}
    .section-title h2 {{ margin: 0; font-size: 27px; line-height: 1.25; }}
    .section-title p {{ margin: 10px 0 0; color: #6f7887; font-size: 16px; font-weight: 650; }}
    .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; width: min(760px, 100%); margin: 0 auto; }}
    .card {{
      min-height: 188px;
      padding: 24px;
      background: #fff;
      border: 1px solid #dde2e8;
      border-radius: 8px;
      box-shadow: 0 16px 42px rgba(31, 42, 56, .04);
    }}
    .icon {{ font-size: 24px; line-height: 1; }}
    .card h3 {{ margin: 18px 0 10px; font-size: 21px; }}
    .card p {{ margin: 0; color: #687385; line-height: 1.65; font-weight: 650; }}
    .tags {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }}
    .tag {{ padding: 5px 9px; color: #6e7784; background: #f3f5f7; border-radius: 999px; font-size: 12px; font-weight: 800; }}
    .protocols {{ width: min(760px, 100%); margin: 0 auto; overflow: hidden; background: #fff; border: 1px solid #dde2e8; border-radius: 8px; }}
    .protocol {{ display: grid; grid-template-columns: 170px 1fr; gap: 24px; padding: 20px 24px; border-bottom: 1px solid #e5e8ed; }}
    .protocol:last-child {{ border-bottom: 0; }}
    .protocol strong {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    .protocol span {{ color: #687385; font-weight: 650; line-height: 1.55; }}
    .pricing {{ width: min(760px, 100%); margin: 0 auto; padding: 42px 28px; text-align: center; color: #687385; background: #fff; border: 1px dashed #d8dde5; border-radius: 8px; font-weight: 700; }}
    .cta {{ text-align: center; }}
    .cta h2 {{ margin: 0; font-size: 28px; }}
    .cta p {{ margin: 10px 0 24px; color: #687385; font-weight: 650; }}
    footer {{ border-top: 1px solid #e2e6ec; padding: 26px 0; color: #98a1af; font-size: 13px; font-weight: 700; }}
    .footer-inner {{ display: flex; align-items: center; justify-content: space-between; gap: 18px; }}
    .footer-links {{ display: flex; gap: 24px; }}
    @media (max-width: 760px) {{
      .nav-links {{ display: none; }}
      .hero {{ grid-template-columns: 1fr; min-height: auto; padding-top: 54px; gap: 28px; }}
      h1 {{ font-size: 36px; }}
      .art {{ min-height: 220px; }}
      .cards {{ grid-template-columns: 1fr; }}
      .protocol {{ grid-template-columns: 1fr; gap: 8px; }}
      .footer-inner {{ align-items: flex-start; flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <nav class="nav">
    <div class="shell nav-inner">
      <a class="brand" href="/"><span class="mark">ID</span><span>{html.escape(SERVICE_NAME)}</span></a>
      <div class="nav-links">
        <a href="#features" data-i18n="nav_features">特性</a>
        <a href="#protocols" data-i18n="nav_protocols">协议</a>
        <a href="#pricing" data-i18n="nav_pricing">定价</a>
        <a href="{html.escape(discovery_url)}" data-i18n="nav_docs">文档</a>
      </div>
      <div class="nav-actions">
        <select class="lang" id="languageSelect" aria-label="Language"><option value="zh">简体中文</option><option value="en">English</option></select>
        <a class="button primary" href="/auth/login?redirect=/console" data-i18n="start">开始使用</a>
      </div>
    </div>
  </nav>
  <main class="shell">
    <div class="hero">
      <div>
        <p class="eyebrow" data-i18n="eyebrow">统一身份认证服务</p>
        <h1 data-i18n="hero_title">一次登录，<br>处处通行</h1>
        <p class="lead" data-i18n="hero_lead">为 ChatGPT Team 提供安全、标准的 OIDC 身份认证。没有冗余配置，只有恰到好处的简洁。</p>
        <div class="hero-actions">
          <a class="button primary" href="/auth/login?redirect=/console" data-i18n="free_start">免费开始使用</a>
          <a class="button secondary" href="{html.escape(discovery_url)}" data-i18n="docs">接入文档</a>
        </div>
      </div>
      <div class="art" aria-hidden="true">
        <div class="shape one"></div><div class="shape two"></div><div class="shape three"></div><div class="line a"></div><div class="line b"></div>
      </div>
    </div>
    <section id="features">
      <div class="section-title"><h2 data-i18n="features_title">核心特性</h2><p data-i18n="features_sub">覆盖身份管理的每个环节</p></div>
      <div class="cards">
        <article class="card"><div class="icon">◇</div><h3 data-i18n="card_auth">安全认证</h3><p data-i18n="card_auth_text">标准 OIDC 授权码流程，返回可验证的身份声明。</p><div class="tags"><span class="tag">OIDC</span><span class="tag">RS256</span></div></article>
        <article class="card"><div class="icon">⌁</div><h3 data-i18n="card_sso">联邦登录</h3><p data-i18n="card_sso_text">一次登录，全站通行，让用户体验更顺滑。</p><div class="tags"><span class="tag">SSO</span><span class="tag">Team</span></div></article>
        <article class="card"><div class="icon">□</div><h3 data-i18n="card_control">权限管控</h3><p data-i18n="card_control_text">按域名和邮箱前缀限制访问，适合小团队快速接入。</p><div class="tags"><span class="tag">Domain</span><span class="tag">Prefix</span></div></article>
      </div>
    </section>
    <section id="protocols">
      <div class="section-title"><h2 data-i18n="protocol_title">协议支持</h2><p data-i18n="protocol_sub">行业标准，全面兼容</p></div>
      <div class="protocols">
        <div class="protocol"><strong>OAuth 2.0</strong><span data-i18n="oauth_text">授权码流程，适配 ChatGPT Team 的回调配置。</span></div>
        <div class="protocol"><strong>OpenID Connect</strong><span data-i18n="oidc_text">基于 OAuth 2 的身份层，提供标准化用户身份断言。</span></div>
        <div class="protocol"><strong>JWKS</strong><span data-i18n="jwks_text">公开 RSA 公钥，便于服务端校验 ID Token 签名。</span></div>
      </div>
    </section>
    <section id="pricing">
      <div class="section-title"><h2 data-i18n="pricing_title">定价</h2><p data-i18n="pricing_sub">按需选择，随时升级</p></div>
      <div class="pricing" data-i18n="pricing_text">当前没有付费计划，所有功能均可免费使用。</div>
    </section>
    <section class="cta">
      <h2 data-i18n="cta_title">准备好简化身份管理了吗？</h2>
      <p data-i18n="cta_sub">从注册到接入，只需几分钟</p>
      <a class="button primary" href="/auth/login?redirect=/console" data-i18n="start_now">立即开始</a>
    </section>
  </main>
  <footer><div class="shell footer-inner"><span>© 2026 {html.escape(SERVICE_NAME)}. All rights reserved.</span><div class="footer-links"><a href="/">首页</a><a href="{html.escape(discovery_url)}">文档</a><a href="/auth/login?redirect=/console">控制台</a></div></div></footer>
<script>
  const copy = {{
    zh: {{
      nav_features: "特性", nav_protocols: "协议", nav_pricing: "定价", nav_docs: "文档", start: "开始使用",
      eyebrow: "统一身份认证服务", hero_title: "一次登录，\\n处处通行", hero_lead: "为 ChatGPT Team 提供安全、标准的 OIDC 身份认证。没有冗余配置，只有恰到好处的简洁。",
      free_start: "免费开始使用", docs: "接入文档", features_title: "核心特性", features_sub: "覆盖身份管理的每个环节",
      card_auth: "安全认证", card_auth_text: "标准 OIDC 授权码流程，返回可验证的身份声明。", card_sso: "联邦登录", card_sso_text: "一次登录，全站通行，让用户体验更顺滑。",
      card_control: "权限管控", card_control_text: "按域名和邮箱前缀限制访问，适合小团队快速接入。", protocol_title: "协议支持", protocol_sub: "行业标准，全面兼容",
      oauth_text: "授权码流程，适配 ChatGPT Team 的回调配置。", oidc_text: "基于 OAuth 2 的身份层，提供标准化用户身份断言。", jwks_text: "公开 RSA 公钥，便于服务端校验 ID Token 签名。",
      pricing_title: "定价", pricing_sub: "按需选择，随时升级", pricing_text: "当前没有付费计划，所有功能均可免费使用。", cta_title: "准备好简化身份管理了吗？", cta_sub: "从注册到接入，只需几分钟", start_now: "立即开始"
    }},
    en: {{
      nav_features: "Features", nav_protocols: "Protocols", nav_pricing: "Pricing", nav_docs: "Docs", start: "Start",
      eyebrow: "Unified identity service", hero_title: "One login,\\neverywhere", hero_lead: "Secure, standard OIDC identity for ChatGPT Team. Less configuration, more clarity.",
      free_start: "Start free", docs: "Docs", features_title: "Core features", features_sub: "Identity management without the clutter",
      card_auth: "Secure auth", card_auth_text: "Standard OIDC authorization code flow with verifiable identity claims.", card_sso: "Federated login", card_sso_text: "One login across the experience, with a smoother user journey.",
      card_control: "Access control", card_control_text: "Restrict access by domain and email prefix for small team rollout.", protocol_title: "Protocol support", protocol_sub: "Standards-based and compatible",
      oauth_text: "Authorization code flow for ChatGPT Team callback configuration.", oidc_text: "OAuth 2 identity layer with standardized user assertions.", jwks_text: "Public RSA keys for ID Token signature verification.",
      pricing_title: "Pricing", pricing_sub: "Start simple, upgrade when needed", pricing_text: "There is no paid plan yet. All features are free to use.", cta_title: "Ready to simplify identity?", cta_sub: "From registration to integration in minutes", start_now: "Start now"
    }}
  }};
  const selector = document.getElementById("languageSelect");
  const applyLang = (lang) => {{
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n]").forEach((node) => {{
      const value = copy[lang][node.dataset.i18n];
      if (value) node.innerText = value;
    }});
  }};
  selector.addEventListener("change", () => applyLang(selector.value));
  applyLang(selector.value);
</script>
</body>
</html>
"""


def html_page(query: dict, error: Optional[str] = None, preview: bool = False) -> str:
    hidden = "\n".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(str(v))}">'
        for k, v in query.items()
    )
    error_block = f'<p class="error">{html.escape(error)}</p>' if error else ""
    domains = EMAIL_DOMAINS or [EMAIL_DOMAIN]
    domain_options = "\n".join(
        f'<option value="{html.escape(domain)}">{html.escape(domain)}</option>'
        for domain in domains
    )
    domain_hint = ", ".join(domains) or "not configured"
    disabled = ""
    preview_alert = (
        '<p class="notice" data-i18n="preview_notice">Use the shared password to login or register, then enter the console.</p>'
        if preview
        else ""
    )
    form_action = "/auth/login" if preview else "/authorize"
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Login | {html.escape(SERVICE_NAME)}</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    html, body {{ min-height: 100%; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: #172033;
      background: #e7edf4;
    }}
    .login-page {{
      min-height: 100vh;
      display: flex;
      align-items: center;
      position: relative;
      padding: 56px min(8vw, 96px);
      overflow: hidden;
      background-image:
        linear-gradient(90deg, rgba(7, 16, 31, .42), rgba(24, 65, 103, .12) 46%, rgba(3, 14, 29, .08)),
        url("{html.escape(LOGIN_BACKGROUND_URL)}");
      background-position: center;
      background-size: cover;
    }}
    .login-page::before {{
      content: "";
      position: absolute;
      inset: 0;
      background:
        linear-gradient(180deg, rgba(255,255,255,.18), rgba(255,255,255,.03) 48%, rgba(15,23,42,.26));
      pointer-events: none;
    }}
    .topbar {{
      position: absolute;
      z-index: 2;
      top: 24px;
      left: min(8vw, 96px);
      right: min(8vw, 96px);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      color: rgba(255,255,255,.88);
    }}
    .top-brand {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      font-weight: 800;
      text-shadow: 0 2px 18px rgba(0,0,0,.24);
    }}
    .top-mark {{
      display: grid;
      place-items: center;
      width: 34px;
      height: 34px;
      border-radius: 9px;
      color: #1d2736;
      background: rgba(255,255,255,.86);
      box-shadow: 0 12px 28px rgba(3, 10, 24, .20);
    }}
    .language-select {{
      min-height: 34px;
      width: auto;
      padding: 6px 30px 6px 10px;
      color: rgba(255,255,255,.94);
      background: rgba(255,255,255,.14);
      border-color: rgba(255,255,255,.32);
      border-radius: 8px;
      backdrop-filter: blur(14px);
    }}
    .language-select option {{ color: #172033; }}
    .login-card {{
      position: relative;
      z-index: 1;
      width: min(460px, calc(100vw - 32px));
      margin-top: 42px;
      padding: 34px 38px;
      background: rgba(238, 248, 255, .55);
      border: 1px solid rgba(255, 255, 255, .72);
      border-radius: 8px;
      box-shadow: 0 28px 80px rgba(8, 24, 44, .26), inset 0 1px 0 rgba(255,255,255,.56);
      backdrop-filter: blur(18px);
    }}
    .brand-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 22px;
    }}
    .brand {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }}
    .brand-mark {{
      display: grid;
      place-items: center;
      width: 38px;
      height: 38px;
      flex: 0 0 auto;
      color: #fff;
      background: #23272f;
      border-radius: 10px;
      font-weight: 700;
      font-size: 13px;
      box-shadow: 0 10px 24px rgba(35,39,47,.22);
    }}
    .brand-text {{
      min-width: 0;
    }}
    .brand-name {{
      margin: 0;
      font-size: 17px;
      font-weight: 700;
      line-height: 1.25;
    }}
    .brand-meta {{
      margin: 2px 0 0;
      color: #616b76;
      font-size: 13px;
      line-height: 1.3;
    }}
    h1 {{
      margin: 0;
      font-size: 28px;
      line-height: 1.18;
      font-weight: 700;
      color: #171a20;
    }}
    .lead {{
      margin: 10px 0 22px;
      color: #4d5966;
      font-size: 15px;
      line-height: 1.65;
    }}
    .tabs {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 0;
      margin: 18px 0 16px;
      border-bottom: 1px solid rgba(255,255,255,.72);
    }}
    .tab-button {{
      width: auto;
      min-height: 40px;
      margin: 0;
      color: #33455f;
      background: transparent;
      box-shadow: none;
      border-radius: 0;
      border-bottom: 2px solid transparent;
    }}
    .tab-button:hover:not(:disabled) {{
      transform: none;
      box-shadow: none;
    }}
    .tab-button.active {{
      color: #1666c5;
      border-color: #1666c5;
    }}
    .register-only, .forgot-panel {{ display: none; }}
    body[data-mode="register"] .register-only {{ display: block; }}
    body[data-mode="forgot"] .login-form {{ display: none; }}
    body[data-mode="forgot"] .forgot-panel {{ display: block; }}
    label {{
      display: block;
      margin: 14px 0 7px;
      color: #28313c;
      font-size: 14px;
      font-weight: 700;
    }}
    input, select {{
      width: 100%;
      min-height: 44px;
      padding: 10px 12px;
      color: #1f2937;
      background: rgba(255,255,255,.76);
      border: 1px solid rgba(148, 163, 184, .56);
      border-radius: 10px;
      font: inherit;
      font-size: 15px;
      outline: none;
      transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
    }}
    input:focus, select:focus {{
      background: rgba(255,255,255,.92);
      border-color: #4e7c92;
      box-shadow: 0 0 0 4px rgba(78, 124, 146, .16);
    }}
    button {{
      width: 100%;
      min-height: 46px;
      margin-top: 18px;
      border: 0;
      border-radius: 10px;
      color: #fff;
      background: #23272f;
      font: inherit;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 14px 28px rgba(23, 96, 173, .24);
      transition: transform .16s ease, opacity .16s ease, box-shadow .16s ease;
    }}
    button:hover:not(:disabled) {{
      transform: translateY(-1px);
      opacity: .94;
      box-shadow: 0 18px 34px rgba(35, 39, 47, .28);
    }}
    button:disabled {{
      cursor: not-allowed;
      opacity: .58;
      box-shadow: none;
    }}
    .hint, .footnote {{
      color: #5d6874;
      font-size: 13px;
      line-height: 1.55;
    }}
    .footnote {{
      margin: 16px 0 0;
    }}
    .error, .notice {{
      margin: 0 0 14px;
      padding: 10px 12px;
      border-radius: 10px;
      font-size: 13px;
      line-height: 1.5;
    }}
    .error {{
      color: #8a241f;
      background: rgba(254, 226, 226, .82);
      border: 1px solid rgba(248, 113, 113, .28);
    }}
    .notice {{
      color: #304456;
      background: rgba(239, 246, 255, .78);
      border: 1px solid rgba(96, 165, 250, .26);
    }}
    code {{ overflow-wrap: anywhere; }}
    .footer {{
      position: absolute;
      z-index: 1;
      left: min(8vw, 96px);
      bottom: 24px;
      width: min(460px, calc(100vw - 32px));
      padding: 10px 14px;
      color: rgba(39, 52, 70, .78);
      text-align: center;
      background: rgba(238,248,255,.55);
      border: 1px solid rgba(255,255,255,.60);
      border-radius: 8px;
      backdrop-filter: blur(14px);
    }}
    @media (max-width: 520px) {{
      .login-page {{ align-items: center; padding: 72px 12px 88px; }}
      .topbar {{ left: 12px; right: 12px; top: 16px; }}
      .login-card {{ padding: 24px; }}
      .brand-row {{ align-items: flex-start; }}
      .footer {{ left: 12px; bottom: 16px; }}
      h1 {{ font-size: 24px; }}
    }}
  </style>
</head>
<body data-mode="login">
<div class="login-page">
  <header class="topbar">
    <div class="top-brand"><span class="top-mark">ID</span><span>{html.escape(SERVICE_NAME)}</span></div>
    <select class="language-select" id="languageSelect" aria-label="Language">
      <option value="zh">简体中文</option>
      <option value="en">English</option>
    </select>
  </header>
  <main class="login-card">
    <div class="brand-row">
      <div class="brand">
        <div class="brand-mark">SSO</div>
        <div class="brand-text">
          <p class="brand-name">{html.escape(SERVICE_NAME)}</p>
          <p class="brand-meta" data-i18n="brand_meta">统一身份认证</p>
        </div>
      </div>
    </div>
    <h1 data-i18n="title">欢迎回来</h1>
    <p class="lead" data-i18n="lead">请先登录，继续回到 ChatGPT。</p>
    {preview_alert}
    {error_block}
    <nav class="tabs" aria-label="Account actions">
      <button class="tab-button active" type="button" data-mode-target="login" data-i18n="tab_login">登录</button>
      <button class="tab-button" type="button" data-mode-target="register" data-i18n="tab_register">注册</button>
      <button class="tab-button" type="button" data-mode-target="forgot" data-i18n="tab_forgot">找回密码</button>
    </nav>
    <form class="login-form" method="post" action="{form_action}">
      {hidden}
      <input type="hidden" id="modeField" name="mode" value="login">
      <div class="register-only">
        <label for="display_name" data-i18n="display_name">显示名称</label>
        <input id="display_name" name="display_name" autocomplete="name" placeholder="Komorebi" {disabled}>
      </div>
      <label for="prefix" data-i18n="prefix_label">邮箱前缀</label>
      <input id="prefix" name="prefix" autocomplete="username" placeholder="alice" required autofocus {disabled}>
      <label for="domain" data-i18n="domain_label">邮箱域名</label>
      <select id="domain" name="domain" required {disabled}>
        {domain_options}
      </select>
      <label for="password" data-i18n="password_label">共享密码</label>
      <input id="password" name="password" type="password" autocomplete="current-password" placeholder="请输入共享密码" required {disabled}>
      <button type="submit" {disabled} data-i18n="submit_login">登录</button>
    </form>
    <section class="forgot-panel">
      <p class="notice" data-i18n="forgot_notice">请联系管理员重置共享密码或确认允许的邮箱前缀。</p>
      <p class="footnote">Discovery: <code>/.well-known/openid-configuration</code></p>
    </section>
    <p class="footnote"><span data-i18n="allowed_domains">允许域名</span>: <code>{html.escape(domain_hint)}</code></p>
  </main>
  <footer class="footer">Copyright © 2026 {html.escape(SERVICE_NAME)}. All rights reserved.</footer>
</div>
<script>
  const i18n = {{
    zh: {{
      brand_meta: "统一身份认证",
      title: "欢迎回来",
      lead: "请先登录，继续回到 ChatGPT。",
      preview_notice: "使用共享密码登录或注册，然后进入控制台。",
      tab_login: "登录",
      tab_register: "注册",
      tab_forgot: "找回密码",
      display_name: "显示名称",
      prefix_label: "邮箱前缀",
      domain_label: "邮箱域名",
      password_label: "共享密码",
      submit_login: "登录",
      submit_register: "注册并继续",
      forgot_notice: "请联系管理员重置共享密码或确认允许的邮箱前缀。",
      allowed_domains: "允许域名"
    }},
    en: {{
      brand_meta: "Unified identity",
      title: "Welcome back",
      lead: "Sign in first, then continue back to ChatGPT.",
      preview_notice: "Use the shared password to login or register, then enter the console.",
      tab_login: "Login",
      tab_register: "Register",
      tab_forgot: "Recover",
      display_name: "Display name",
      prefix_label: "Email prefix",
      domain_label: "Email domain",
      password_label: "Shared password",
      submit_login: "Login",
      submit_register: "Register and continue",
      forgot_notice: "Contact your administrator to reset the shared password or confirm allowed email prefixes.",
      allowed_domains: "Allowed domains"
    }}
  }};
  const languageSelect = document.getElementById("languageSelect");
  const modeField = document.getElementById("modeField");
  const submitButton = document.querySelector(".login-form button[type='submit']");
  const setLanguage = (lang) => {{
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n]").forEach((node) => {{
      node.textContent = i18n[lang][node.dataset.i18n] || node.textContent;
    }});
    submitButton.textContent = document.body.dataset.mode === "register" ? i18n[lang].submit_register : i18n[lang].submit_login;
  }};
  const setMode = (mode) => {{
    document.body.dataset.mode = mode;
    modeField.value = mode;
    document.querySelectorAll(".tab-button").forEach((button) => {{
      button.classList.toggle("active", button.dataset.modeTarget === mode);
    }});
    setLanguage(languageSelect.value);
  }};
  document.querySelectorAll(".tab-button").forEach((button) => {{
    button.addEventListener("click", () => setMode(button.dataset.modeTarget));
  }});
  languageSelect.addEventListener("change", () => setLanguage(languageSelect.value));
  setLanguage(languageSelect.value);
</script>
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
    return HTMLResponse(root_page())


@app.get("/auth/login", response_class=HTMLResponse)
def login_preview(redirect: str = "/console"):
    return html_page({"redirect": safe_local_redirect(redirect)}, preview=True)


@app.post("/auth/login", response_class=HTMLResponse)
def login_submit(
    redirect: str = Form("/console"),
    prefix: str = Form(...),
    domain: str = Form(...),
    password: str = Form(...),
    mode: str = Form("login"),
    display_name: str = Form(""),
    invite_code: str = Form(""),
):
    target = safe_local_redirect(redirect)
    query = {"redirect": target}
    try:
        email, _profile = authenticate_or_register(
            mode=mode,
            prefix=prefix,
            domain=domain,
            password=password,
            display_name=display_name,
            invite_code=invite_code,
        )
    except ValueError as exc:
        return html_page(query, str(exc), preview=True)

    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        "sso_user",
        email,
        max_age=12 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=ISSUER.startswith("https://"),
    )
    return response


@app.get("/console", response_class=HTMLResponse)
def console(request: Request):
    if is_admin_request(request):
        return HTMLResponse(render_admin_console())

    email = request.cookies.get("sso_user", "")
    profile = profiles.get(email, {})
    if not email:
        return RedirectResponse("/auth/login?redirect=/console", status_code=303)
    display_name = profile.get("name") or email
    return HTMLResponse(render_user_console(email, profile))
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Console | {html.escape(SERVICE_NAME)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: #172033;
      background-image: linear-gradient(90deg, rgba(7,16,31,.46), rgba(24,65,103,.10)), url("{html.escape(LOGIN_BACKGROUND_URL)}");
      background-position: center;
      background-size: cover;
    }}
    main {{
      width: min(560px, 100%);
      padding: 34px;
      background: rgba(238, 248, 255, .66);
      border: 1px solid rgba(255,255,255,.74);
      border-radius: 8px;
      box-shadow: 0 28px 80px rgba(8,24,44,.26);
      backdrop-filter: blur(18px);
    }}
    h1 {{ margin: 0 0 12px; font-size: 28px; }}
    p {{ margin: 0 0 18px; color: #526071; line-height: 1.65; }}
    code {{ padding: 3px 6px; background: rgba(255,255,255,.64); border-radius: 6px; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    a {{ display: inline-flex; align-items: center; justify-content: center; min-height: 40px; padding: 0 16px; color: #fff; background: #171c27; border-radius: 8px; text-decoration: none; font-weight: 800; }}
    a.secondary {{ color: #172033; background: rgba(255,255,255,.68); }}
  </style>
</head>
<body>
  <main>
    <h1>控制台</h1>
    <p>已登录为 <code>{html.escape(display_name)}</code></p>
    <p>OIDC Discovery: <code>/.well-known/openid-configuration</code></p>
    <div class="actions"><a href="/">返回首页</a><a class="secondary" href="/admin/login">管理员入口</a></div>
  </main>
</body>
</html>"""
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    if is_admin_request(request):
        return RedirectResponse("/console", status_code=303)
    return RedirectResponse("/admin/login?redirect=/console", status_code=303)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(error: str = "", redirect: str = "/console"):
    return HTMLResponse(render_admin_login(error=error, redirect=safe_local_redirect(redirect)))


@app.post("/admin/login", response_class=HTMLResponse)
def admin_login_submit(
    username: str = Form(...),
    password: str = Form(...),
    redirect: str = Form("/console"),
):
    target = safe_local_redirect(redirect)
    if not ADMIN_PASSWORD or username.strip() != ADMIN_USERNAME or not secrets.compare_digest(password, ADMIN_PASSWORD):
        return HTMLResponse(render_admin_login(error="管理员账号或密码错误。", redirect=target), status_code=401)
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        "admin_auth",
        make_admin_token(),
        max_age=12 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=ISSUER.startswith("https://"),
    )
    return response


@app.post("/admin/invites", response_class=HTMLResponse)
def admin_create_invite(
    request: Request,
    note: str = Form(""),
    max_uses: int = Form(1),
    expires_days: int = Form(7),
):
    if not is_admin_request(request):
        return RedirectResponse("/admin/login?redirect=/console", status_code=303)
    code = make_invite_code()
    while code in invitations:
        code = make_invite_code()
    max_uses = max(1, min(int(max_uses or 1), 999))
    expires_days = max(0, min(int(expires_days or 0), 365))
    invitations[code] = {
        "code": code,
        "note": note.strip(),
        "max_uses": max_uses,
        "uses": 0,
        "active": True,
        "created_at": now_ts(),
        "expires_at": now_ts() + expires_days * 86400 if expires_days else 0,
        "used_by": [],
    }
    save_invitations()
    return RedirectResponse("/console", status_code=303)


@app.post("/admin/settings", response_class=HTMLResponse)
def admin_update_settings(
    request: Request,
    invite_required: str = Form(""),
):
    if not is_admin_request(request):
        return RedirectResponse("/admin/login?redirect=/console", status_code=303)
    app_settings["invite_required"] = invite_required == "on"
    save_settings()
    return RedirectResponse("/console", status_code=303)


@app.post("/admin/invites/{code}/toggle", response_class=HTMLResponse)
def admin_toggle_invite(request: Request, code: str):
    if not is_admin_request(request):
        return RedirectResponse("/admin/login?redirect=/console", status_code=303)
    key = clean_invite_code(code)
    if key in invitations:
        invitations[key]["active"] = not invitations[key].get("active", True)
        save_invitations()
    return RedirectResponse("/console", status_code=303)


@app.post("/admin/logout", response_class=HTMLResponse)
def admin_logout():
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie("admin_auth")
    return response


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
    mode: str = Form("login"),
    display_name: str = Form(""),
    invite_code: str = Form(""),
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
    try:
        email, profile = authenticate_or_register(
            mode=mode,
            prefix=prefix,
            domain=domain,
            password=password,
            display_name=display_name,
            invite_code=invite_code,
        )
    except ValueError as exc:
        return html_page(query, str(exc))

    normalized_prefix = prefix.strip().lower()

    code = secrets.token_urlsafe(32)
    codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "nonce": nonce,
        "email": email,
        "prefix": normalized_prefix,
        "name": profile.get("name") or email,
        "iat": int(time.time()),
    }
    params = {"code": code}
    if state:
        params["state"] = state
    return RedirectResponse(redirect_with_params(redirect_uri, params), status_code=303)


def render_admin_login(error: str = "", redirect: str = "/console") -> str:
    error_block = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>管理员登录 | {html.escape(SERVICE_NAME)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: #111827;
      background:
        linear-gradient(90deg, rgba(8,17,35,.50), rgba(22,82,135,.12), rgba(255,214,218,.22)),
        url("{html.escape(LOGIN_BACKGROUND_URL)}");
      background-position: center;
      background-size: cover;
    }}
    main {{
      width: min(430px, 100%);
      padding: 32px;
      border: 1px solid rgba(255,255,255,.68);
      border-radius: 8px;
      background: rgba(242,249,255,.68);
      box-shadow: 0 30px 86px rgba(8,24,44,.30);
      backdrop-filter: blur(22px);
    }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    p {{ margin: 0 0 20px; color: #5c6675; line-height: 1.6; }}
    label {{ display: block; margin: 14px 0 7px; font-weight: 900; }}
    input {{ width: 100%; min-height: 44px; padding: 10px 12px; border: 1px solid rgba(148,163,184,.58); border-radius: 8px; font: inherit; }}
    button {{ width: 100%; min-height: 46px; margin-top: 18px; border: 0; border-radius: 8px; color: #fff; background: #171c27; font: inherit; font-weight: 900; cursor: pointer; }}
    .error {{ margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; color: #8a241f; background: rgba(254,226,226,.84); border: 1px solid rgba(248,113,113,.30); }}
    a {{ color: #172033; font-weight: 800; text-decoration: none; }}
  </style>
</head>
<body>
  <main>
    <h1>管理员登录</h1>
    <p>登录后可生成邀请码、管理用户和查看 OIDC 接入信息。</p>
    {error_block}
    <form method="post" action="/admin/login">
      <input type="hidden" name="redirect" value="{html.escape(redirect)}">
      <label for="username">管理员账号</label>
      <input id="username" name="username" autocomplete="username" required autofocus>
      <label for="password">管理员密码</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">登录后台</button>
    </form>
    <p style="margin-top:16px"><a href="/">返回首页</a></p>
  </main>
</body>
</html>"""


def render_admin_console() -> str:
    invite_rows = []
    for invite in sorted(invitations.values(), key=lambda item: int(item.get("created_at") or 0), reverse=True):
        code = html.escape(invite.get("code", ""))
        status = "启用" if invite.get("active", True) else "停用"
        note = html.escape(invite.get("note") or "-")
        uses = int(invite.get("uses") or 0)
        max_uses = int(invite.get("max_uses") or 1)
        expires = fmt_time(invite.get("expires_at"))
        created = fmt_time(invite.get("created_at"))
        invite_rows.append(f"""
          <tr>
            <td><code>{code}</code></td>
            <td>{note}</td>
            <td>{uses}/{max_uses}</td>
            <td>{expires}</td>
            <td><span class="pill">{status}</span></td>
            <td>{created}</td>
            <td><form method="post" action="/admin/invites/{code}/toggle"><button class="ghost" type="submit">{"停用" if invite.get("active", True) else "启用"}</button></form></td>
          </tr>""")
    if not invite_rows:
        invite_rows.append('<tr><td colspan="7" class="empty">还没有邀请码，先生成一个。</td></tr>')

    user_rows = []
    for email, profile in sorted(profiles.items(), key=lambda item: int(item[1].get("registered_at") or 0), reverse=True):
        user_rows.append(f"""
          <tr>
            <td>{html.escape(email)}</td>
            <td>{html.escape(profile.get("name") or email)}</td>
            <td>{fmt_time(profile.get("registered_at"))}</td>
            <td>{fmt_time(profile.get("last_login_at"))}</td>
          </tr>""")
    if not user_rows:
        user_rows.append('<tr><td colspan="4" class="empty">暂无注册用户。</td></tr>')

    active_invites = sum(1 for item in invitations.values() if item.get("active", True) and invite_available(item.get("code", ""))[0])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>管理后台 | {html.escape(SERVICE_NAME)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: #111827;
      background:
        linear-gradient(180deg, rgba(255,255,255,.40), rgba(235,246,255,.18) 38%, rgba(6,17,36,.36)),
        url("{html.escape(LOGIN_BACKGROUND_URL)}");
      background-position: center;
      background-size: cover;
      background-attachment: fixed;
    }}
    .topbar {{
      position: sticky;
      top: 0;
      z-index: 4;
      display: flex;
      justify-content: space-between;
      align-items: center;
      min-height: 68px;
      padding: 0 min(5vw, 56px);
      border-bottom: 1px solid rgba(255,255,255,.42);
      background: rgba(245,250,255,.62);
      backdrop-filter: blur(18px);
    }}
    .brand {{ display: flex; align-items: center; gap: 10px; font-weight: 900; }}
    .mark {{ display: grid; place-items: center; width: 36px; height: 36px; color: #fff; background: #171c27; border-radius: 8px; }}
    .layout {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 34px 0 64px; }}
    h1 {{ margin: 0 0 8px; color: #fff; font-size: 42px; text-shadow: 0 16px 44px rgba(8,24,44,.34); }}
    .lead {{ margin: 0 0 24px; color: rgba(255,255,255,.90); font-weight: 700; text-shadow: 0 10px 28px rgba(8,24,44,.30); }}
    .stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 18px; }}
    .stat, .panel {{ border: 1px solid rgba(255,255,255,.66); border-radius: 8px; background: rgba(242,249,255,.70); box-shadow: 0 22px 70px rgba(8,24,44,.22); backdrop-filter: blur(20px); }}
    .stat {{ padding: 20px; }}
    .stat b {{ display: block; font-size: 30px; }}
    .stat span {{ color: #5c6675; font-weight: 800; }}
    .grid {{ display: grid; grid-template-columns: 360px 1fr; gap: 18px; align-items: start; }}
    .panel {{ padding: 22px; overflow: hidden; }}
    h2 {{ margin: 0 0 16px; font-size: 22px; }}
    label {{ display: block; margin: 13px 0 7px; font-weight: 900; }}
    input {{ width: 100%; min-height: 42px; padding: 10px 12px; border: 1px solid rgba(148,163,184,.58); border-radius: 8px; font: inherit; }}
    button, .link-button {{ min-height: 38px; padding: 0 14px; border: 0; border-radius: 8px; color: #fff; background: #171c27; font: inherit; font-weight: 900; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; justify-content: center; }}
    button.ghost {{ color: #172033; background: rgba(255,255,255,.72); border: 1px solid rgba(148,163,184,.38); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 12px 10px; border-bottom: 1px solid rgba(148,163,184,.28); text-align: left; vertical-align: middle; }}
    th {{ color: #516071; font-size: 12px; text-transform: uppercase; }}
    code {{ padding: 3px 6px; border-radius: 6px; background: rgba(255,255,255,.66); }}
    .pill {{ display: inline-flex; padding: 4px 8px; border-radius: 999px; background: rgba(22,119,255,.12); color: #1457b8; font-weight: 900; font-size: 12px; }}
    .empty {{ color: #64748b; text-align: center; }}
    .stack {{ display: grid; gap: 18px; }}
    .top-actions {{ display: flex; gap: 10px; align-items: center; }}
    @media (max-width: 900px) {{
      .stats, .grid {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 34px; }}
      .topbar {{ align-items: flex-start; flex-direction: column; padding: 14px 16px; gap: 12px; }}
    }}
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand"><span class="mark">SSO</span><span>{html.escape(SERVICE_NAME)}</span></div>
    <div class="top-actions">
      <a class="link-button" href="/">首页</a>
      <form method="post" action="/admin/logout"><button class="ghost" type="submit">退出</button></form>
    </div>
  </header>
  <main class="layout">
    <h1>管理后台</h1>
    <p class="lead">生成邀请码、查看注册用户，并管理 OIDC 接入状态。</p>
    <section class="stats">
      <div class="stat"><b>{len(profiles)}</b><span>注册用户</span></div>
      <div class="stat"><b>{len(invitations)}</b><span>邀请码总数</span></div>
      <div class="stat"><b>{active_invites}</b><span>可用邀请码</span></div>
    </section>
    <section class="grid">
      <div class="stack">
        <section class="panel">
          <h2>生成邀请码</h2>
          <form method="post" action="/admin/invites">
            <label for="note">备注</label>
            <input id="note" name="note" placeholder="例如：6 月新用户">
            <label for="max_uses">可用次数</label>
            <input id="max_uses" name="max_uses" type="number" min="1" max="999" value="1">
            <label for="expires_days">有效天数</label>
            <input id="expires_days" name="expires_days" type="number" min="0" max="365" value="7">
            <button style="width:100%; margin-top:16px" type="submit">生成邀请码</button>
          </form>
        </section>
        <section class="panel">
          <h2>OIDC 信息</h2>
          <p><code>{html.escape(ISSUER or "not configured")}</code></p>
          <p><code>/.well-known/openid-configuration</code></p>
          <p><code>/authorize</code> <code>/token</code> <code>/jwks.json</code></p>
        </section>
      </div>
      <div class="stack">
        <section class="panel">
          <h2>邀请码</h2>
          <table>
            <thead><tr><th>邀请码</th><th>备注</th><th>使用</th><th>过期</th><th>状态</th><th>创建</th><th>操作</th></tr></thead>
            <tbody>{"".join(invite_rows)}</tbody>
          </table>
        </section>
        <section class="panel">
          <h2>用户</h2>
          <table>
            <thead><tr><th>邮箱</th><th>显示名</th><th>注册时间</th><th>最后登录</th></tr></thead>
            <tbody>{"".join(user_rows)}</tbody>
          </table>
        </section>
      </div>
    </section>
  </main>
</body>
</html>"""


def root_page() -> str:
    discovery_url = f"{ISSUER}/.well-known/openid-configuration" if ISSUER else "/.well-known/openid-configuration"
    service = html.escape(SERVICE_NAME)
    discovery = html.escape(discovery_url)
    background = html.escape(LOGIN_BACKGROUND_URL)
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{service} | OIDC SSO</title>
  <style>
    :root {{ color-scheme: light; --ink:#111827; --muted:#5b6472; --line:rgba(255,255,255,.52); --panel:rgba(255,255,255,.58); --accent:#1677ff; }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background:
        linear-gradient(180deg, rgba(255,255,255,.46), rgba(235,246,255,.18) 38%, rgba(6,17,36,.36)),
        linear-gradient(90deg, rgba(9,18,41,.34), rgba(43,104,155,.10) 48%, rgba(255,179,184,.20)),
        url("{background}");
      background-size: cover;
      background-position: center;
      background-attachment: fixed;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        radial-gradient(circle at 76% 18%, rgba(255,255,255,.60), transparent 9%),
        linear-gradient(115deg, transparent 0 50%, rgba(255,255,255,.24) 50.2%, transparent 52%);
    }}
    a {{ color: inherit; text-decoration: none; }}
    .shell {{ width: min(1160px, calc(100% - 40px)); margin: 0 auto; }}
    .nav {{
      position: sticky;
      top: 0;
      z-index: 5;
      border-bottom: 1px solid rgba(255,255,255,.38);
      background: rgba(245,250,255,.60);
      backdrop-filter: blur(18px);
    }}
    .nav-inner {{ min-height: 70px; display: flex; align-items: center; justify-content: space-between; gap: 18px; }}
    .brand {{ display: inline-flex; align-items: center; gap: 10px; font-weight: 800; }}
    .mark {{ display: grid; place-items: center; width: 36px; height: 36px; color: #fff; background: #171c27; border-radius: 8px; box-shadow: 0 12px 28px rgba(17,24,39,.24); }}
    .nav-links {{ display: flex; align-items: center; gap: 28px; color: #324054; font-weight: 700; font-size: 14px; }}
    .nav-actions {{ display: flex; align-items: center; gap: 10px; }}
    select, .button {{
      min-height: 40px;
      border-radius: 8px;
      border: 1px solid rgba(255,255,255,.62);
      font: inherit;
      font-size: 14px;
      font-weight: 800;
      backdrop-filter: blur(14px);
    }}
    select {{ padding: 0 34px 0 12px; color: #172033; background: rgba(255,255,255,.62); }}
    .button {{ display: inline-flex; align-items: center; justify-content: center; padding: 0 18px; }}
    .button.primary {{ color: #fff; border-color: rgba(17,24,39,.88); background: #171c27; box-shadow: 0 14px 30px rgba(17,24,39,.22); }}
    .button.ghost {{ color: #172033; background: rgba(255,255,255,.52); }}
    .hero {{
      min-height: calc(100vh - 70px);
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(320px, 460px);
      align-items: center;
      gap: 56px;
      padding: 66px 0 86px;
    }}
    .eyebrow {{ margin: 0 0 16px; color: rgba(255,255,255,.90); font-size: 14px; font-weight: 900; text-shadow: 0 2px 18px rgba(7,16,31,.34); }}
    h1 {{ margin: 0; max-width: 680px; color: #fff; font-size: 58px; line-height: 1.08; letter-spacing: 0; text-shadow: 0 18px 45px rgba(6,13,28,.34); }}
    .lead {{ max-width: 560px; margin: 22px 0 30px; color: rgba(255,255,255,.88); font-size: 17px; line-height: 1.75; font-weight: 650; text-shadow: 0 8px 26px rgba(6,13,28,.28); }}
    .hero-actions {{ display: flex; flex-wrap: wrap; gap: 12px; }}
    .status-panel {{
      padding: 28px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 28px 80px rgba(8,24,44,.26), inset 0 1px 0 rgba(255,255,255,.62);
      backdrop-filter: blur(20px);
    }}
    .status-panel h2 {{ margin: 0 0 14px; font-size: 23px; }}
    .metric {{ display: grid; grid-template-columns: 92px 1fr; gap: 14px; padding: 14px 0; border-top: 1px solid rgba(255,255,255,.58); color: var(--muted); line-height: 1.5; }}
    .metric strong {{ color: #172033; }}
    .section {{ padding: 74px 0; border-top: 1px solid rgba(255,255,255,.40); background: rgba(248,251,255,.46); backdrop-filter: blur(8px); }}
    .section h2 {{ margin: 0 0 28px; font-size: 30px; }}
    .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }}
    .card {{ min-height: 188px; padding: 24px; border: 1px solid rgba(255,255,255,.60); border-radius: 8px; background: rgba(255,255,255,.56); box-shadow: 0 18px 42px rgba(23,42,70,.10); backdrop-filter: blur(16px); }}
    .card b {{ display: block; margin-bottom: 10px; font-size: 19px; }}
    .card p {{ margin: 0; color: var(--muted); line-height: 1.65; font-weight: 650; }}
    footer {{ padding: 24px 0; color: rgba(255,255,255,.86); text-shadow: 0 2px 14px rgba(6,13,28,.35); }}
    @media (max-width: 820px) {{
      .nav-links {{ display: none; }}
      .hero {{ min-height: auto; grid-template-columns: 1fr; gap: 30px; padding-top: 46px; }}
      h1 {{ font-size: 40px; }}
      .cards {{ grid-template-columns: 1fr; }}
      .nav-inner {{ align-items: flex-start; flex-direction: column; padding: 14px 0; }}
      .nav-actions {{ width: 100%; justify-content: space-between; }}
    }}
  </style>
</head>
<body>
  <nav class="nav">
    <div class="shell nav-inner">
      <a class="brand" href="/"><span class="mark">SSO</span><span>{service}</span></a>
      <div class="nav-links">
        <a href="#features" data-i18n="nav_features">功能</a>
        <a href="#protocol" data-i18n="nav_protocol">协议</a>
        <a href="{discovery}" data-i18n="nav_docs">文档</a>
      </div>
      <div class="nav-actions">
        <select id="languageSelect" aria-label="Language"><option value="zh">简体中文</option><option value="en">English</option></select>
        <a class="button primary" href="/auth/login?redirect=/console" data-i18n="start">开始使用</a>
      </div>
    </div>
  </nav>
  <main>
    <section class="shell hero">
      <div>
        <p class="eyebrow" data-i18n="eyebrow">统一身份认证服务</p>
        <h1 data-i18n="hero_title">一次登录，通往所有服务</h1>
        <p class="lead" data-i18n="hero_lead">为 ChatGPT Team 和内部应用提供漂亮、轻量、标准的 OIDC 单点登录体验。</p>
        <div class="hero-actions">
          <a class="button primary" href="/auth/login?redirect=/console" data-i18n="free_start">开始使用</a>
          <a class="button ghost" href="{discovery}" data-i18n="docs">查看接入文档</a>
        </div>
      </div>
      <aside class="status-panel" id="protocol">
        <h2 data-i18n="panel_title">OIDC 就绪</h2>
        <div class="metric"><strong>Issuer</strong><span>{html.escape(ISSUER or "not configured")}</span></div>
        <div class="metric"><strong>Client</strong><span>{html.escape(CLIENT_ID or "not configured")}</span></div>
        <div class="metric"><strong>Domains</strong><span>{html.escape(", ".join(EMAIL_DOMAINS) or "not configured")}</span></div>
      </aside>
    </section>
    <section class="section" id="features">
      <div class="shell">
        <h2 data-i18n="features_title">登录体验更轻盈</h2>
        <div class="cards">
          <article class="card"><b data-i18n="card_login">登录与注册</b><p data-i18n="card_login_text">同一个入口完成账号登录、首次注册和返回控制台。</p></article>
          <article class="card"><b data-i18n="card_language">中英双语</b><p data-i18n="card_language_text">页面支持简体中文和 English，一键切换并即时生效。</p></article>
          <article class="card"><b data-i18n="card_oidc">标准 OIDC</b><p data-i18n="card_oidc_text">授权码、JWKS、ID Token 保持兼容现有接入方式。</p></article>
        </div>
      </div>
    </section>
  </main>
  <footer><div class="shell">Copyright 2026 {service}. All rights reserved.</div></footer>
<script>
  const copy = {{
    zh: {{
      nav_features:"功能", nav_protocol:"协议", nav_docs:"文档", start:"开始使用", eyebrow:"统一身份认证服务",
      hero_title:"一次登录，通往所有服务", hero_lead:"为 ChatGPT Team 和内部应用提供漂亮、轻量、标准的 OIDC 单点登录体验。",
      free_start:"开始使用", docs:"查看接入文档", panel_title:"OIDC 就绪", features_title:"登录体验更轻盈",
      card_login:"登录与注册", card_login_text:"同一个入口完成账号登录、首次注册和返回控制台。",
      card_language:"中英双语", card_language_text:"页面支持简体中文和 English，一键切换并即时生效。",
      card_oidc:"标准 OIDC", card_oidc_text:"授权码、JWKS、ID Token 保持兼容现有接入方式。"
    }},
    en: {{
      nav_features:"Features", nav_protocol:"Protocol", nav_docs:"Docs", start:"Start", eyebrow:"Unified identity service",
      hero_title:"One login for every service", hero_lead:"A polished, lightweight, standards-based OIDC SSO experience for ChatGPT Team and internal apps.",
      free_start:"Get started", docs:"View docs", panel_title:"OIDC ready", features_title:"A lighter sign-in experience",
      card_login:"Login and register", card_login_text:"Use one entry point to sign in, create an account, and return to the console.",
      card_language:"Chinese and English", card_language_text:"Switch between Simplified Chinese and English instantly.",
      card_oidc:"Standard OIDC", card_oidc_text:"Authorization code, JWKS, and ID Token behavior remain compatible with existing integrations."
    }}
  }};
  const languageSelect = document.getElementById("languageSelect");
  const setLanguage = (lang) => {{
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n]").forEach((node) => {{
      node.textContent = copy[lang][node.dataset.i18n] || node.textContent;
    }});
    localStorage.setItem("sso-language", lang);
  }};
  languageSelect.value = localStorage.getItem("sso-language") || "zh";
  languageSelect.addEventListener("change", () => setLanguage(languageSelect.value));
  setLanguage(languageSelect.value);
</script>
</body>
</html>
"""


def html_page(query: dict, error: Optional[str] = None, preview: bool = False) -> str:
    hidden = "\n".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(str(v))}">'
        for k, v in query.items()
    )
    error_block = f'<p class="error">{html.escape(error)}</p>' if error else ""
    domains = EMAIL_DOMAINS or [EMAIL_DOMAIN]
    domain_options = "\n".join(
        f'<option value="{html.escape(domain)}">{html.escape(domain)}</option>'
        for domain in domains
        if domain
    )
    if not domain_options:
        domain_options = '<option value="">not configured</option>'
    preview_alert = (
        '<p class="notice" data-i18n="preview_notice">登录已有账号，或使用管理员发放的邀请码注册。</p>'
        if preview
        else ""
    )
    form_action = "/auth/login" if preview else "/authorize"
    background = html.escape(LOGIN_BACKGROUND_URL)
    service = html.escape(SERVICE_NAME)
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Login | {service}</title>
  <style>
    :root {{ color-scheme: light; --ink:#111827; --muted:#5c6675; --blue:#1677ff; }}
    * {{ box-sizing: border-box; }}
    html, body {{ min-height: 100%; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgba(8,17,35,.52), rgba(22,82,135,.12) 48%, rgba(255,214,218,.20)),
        linear-gradient(180deg, rgba(255,255,255,.12), rgba(8,17,35,.24)),
        url("{background}");
      background-position: center;
      background-size: cover;
      background-attachment: fixed;
    }}
    a {{ color: inherit; text-decoration: none; }}
    .page {{ min-height: 100vh; display: grid; grid-template-columns: minmax(0, 1fr) minmax(340px, 456px); gap: 56px; align-items: center; padding: 72px min(8vw, 96px); position: relative; overflow: hidden; }}
    .page::before {{ content:""; position:absolute; inset:0; pointer-events:none; background: linear-gradient(116deg, transparent 0 52%, rgba(255,255,255,.22) 52.2%, transparent 53.4%); }}
    .topbar {{ position: absolute; z-index: 2; top: 24px; left: min(8vw, 96px); right: min(8vw, 96px); display: flex; justify-content: space-between; align-items: center; gap: 16px; color: rgba(255,255,255,.94); }}
    .top-brand {{ display: inline-flex; align-items: center; gap: 10px; font-weight: 900; text-shadow: 0 2px 18px rgba(0,0,0,.28); }}
    .top-mark {{ display: grid; place-items: center; width: 36px; height: 36px; border-radius: 8px; color:#162033; background: rgba(255,255,255,.88); box-shadow: 0 12px 28px rgba(3,10,24,.20); }}
    .language-select {{ width: auto; min-height: 38px; padding: 0 34px 0 12px; color: #172033; background: rgba(255,255,255,.72); border: 1px solid rgba(255,255,255,.62); border-radius: 8px; font: inherit; font-size: 14px; font-weight: 800; backdrop-filter: blur(14px); }}
    .intro {{ position: relative; z-index: 1; max-width: 660px; color: #fff; text-shadow: 0 18px 45px rgba(6,13,28,.36); }}
    .intro p {{ margin: 0 0 14px; font-weight: 900; color: rgba(255,255,255,.90); }}
    .intro h1 {{ margin: 0; font-size: 54px; line-height: 1.08; letter-spacing: 0; }}
    .intro .lead {{ max-width: 520px; margin-top: 20px; color: rgba(255,255,255,.90); font-size: 17px; line-height: 1.7; font-weight: 700; }}
    .card {{ position: relative; z-index: 1; width: 100%; padding: 32px 36px; border: 1px solid rgba(255,255,255,.68); border-radius: 8px; background: rgba(242,249,255,.68); box-shadow: 0 30px 86px rgba(8,24,44,.30), inset 0 1px 0 rgba(255,255,255,.58); backdrop-filter: blur(22px); }}
    .brand-row {{ display: flex; align-items: center; gap: 12px; margin-bottom: 22px; }}
    .brand-mark {{ display: grid; place-items: center; width: 42px; height: 42px; flex: 0 0 auto; color: #fff; background: #171c27; border-radius: 8px; font-size: 13px; font-weight: 900; box-shadow: 0 12px 26px rgba(17,24,39,.22); }}
    .brand-name {{ margin: 0; font-size: 17px; font-weight: 900; }}
    .brand-meta {{ margin: 3px 0 0; color: #5c6675; font-size: 13px; font-weight: 700; }}
    h2 {{ margin: 0; font-size: 28px; line-height: 1.18; }}
    .card > .lead {{ margin: 10px 0 18px; color: var(--muted); font-size: 15px; line-height: 1.65; }}
    .tabs {{ display: grid; grid-template-columns: repeat(3, 1fr); margin: 18px 0 16px; border-bottom: 1px solid rgba(255,255,255,.72); }}
    .tab-button {{ min-height: 42px; margin: 0; color: #33455f; background: transparent; border: 0; border-bottom: 2px solid transparent; border-radius: 0; box-shadow: none; font: inherit; font-weight: 900; cursor: pointer; }}
    .tab-button.active {{ color: var(--blue); border-color: var(--blue); }}
    .register-only, .forgot-panel {{ display: none; }}
    body[data-mode="register"] .register-only {{ display: block; }}
    body[data-mode="forgot"] .login-form {{ display: none; }}
    body[data-mode="forgot"] .forgot-panel {{ display: block; }}
    label {{ display: block; margin: 14px 0 7px; color: #263241; font-size: 14px; font-weight: 900; }}
    input, select {{ width: 100%; min-height: 44px; padding: 10px 12px; color: #1f2937; background: rgba(255,255,255,.82); border: 1px solid rgba(148,163,184,.56); border-radius: 8px; font: inherit; font-size: 15px; outline: none; transition: border-color .18s ease, box-shadow .18s ease, background .18s ease; }}
    input:focus, select:focus {{ background: rgba(255,255,255,.96); border-color: #3b82f6; box-shadow: 0 0 0 4px rgba(59,130,246,.16); }}
    .submit {{ width: 100%; min-height: 46px; margin-top: 18px; border: 0; border-radius: 8px; color: #fff; background: #171c27; font: inherit; font-size: 15px; font-weight: 900; cursor: pointer; box-shadow: 0 16px 34px rgba(17,24,39,.26); transition: transform .16s ease, opacity .16s ease, box-shadow .16s ease; }}
    .submit:hover {{ transform: translateY(-1px); opacity: .95; box-shadow: 0 20px 42px rgba(17,24,39,.30); }}
    .error, .notice {{ margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; font-size: 13px; line-height: 1.5; }}
    .error {{ color: #8a241f; background: rgba(254,226,226,.84); border: 1px solid rgba(248,113,113,.30); }}
    .notice {{ color: #24384f; background: rgba(239,246,255,.82); border: 1px solid rgba(96,165,250,.28); }}
    .footnote {{ margin: 16px 0 0; color: var(--muted); font-size: 13px; line-height: 1.55; }}
    code {{ overflow-wrap: anywhere; }}
    @media (max-width: 820px) {{
      .page {{ grid-template-columns: 1fr; gap: 28px; padding: 86px 16px 32px; }}
      .intro h1 {{ font-size: 38px; }}
      .card {{ padding: 26px; }}
      .topbar {{ left: 16px; right: 16px; top: 18px; }}
    }}
  </style>
</head>
<body data-mode="login">
<div class="page">
  <header class="topbar">
    <a class="top-brand" href="/"><span class="top-mark">SSO</span><span>{service}</span></a>
    <select class="language-select" id="languageSelect" aria-label="Language">
      <option value="zh">简体中文</option>
      <option value="en">English</option>
    </select>
  </header>
  <section class="intro" aria-hidden="true">
    <p data-i18n="intro_kicker">欢迎回来</p>
    <h1 data-i18n="intro_title">在星空下继续你的工作流</h1>
    <div class="lead" data-i18n="intro_lead">登录已有账号，或使用管理员发放的邀请码完成注册。</div>
  </section>
  <main class="card">
    <div class="brand-row">
      <div class="brand-mark">ID</div>
      <div>
        <p class="brand-name">{service}</p>
        <p class="brand-meta" data-i18n="brand_meta">统一身份认证</p>
      </div>
    </div>
    <h2 data-i18n="title">登录账号</h2>
    <p class="lead" data-i18n="lead">请输入邮箱前缀和账号密码继续。</p>
    {preview_alert}
    {error_block}
    <nav class="tabs" aria-label="Account actions">
      <button class="tab-button active" type="button" data-mode-target="login" data-i18n="tab_login">登录</button>
      <button class="tab-button" type="button" data-mode-target="register" data-i18n="tab_register">注册</button>
      <button class="tab-button" type="button" data-mode-target="forgot" data-i18n="tab_forgot">找回</button>
    </nav>
    <form class="login-form" method="post" action="{form_action}">
      {hidden}
      <input type="hidden" id="modeField" name="mode" value="login">
      <div class="register-only">
        <label for="display_name" data-i18n="display_name">显示名称</label>
        <input id="display_name" name="display_name" autocomplete="name" placeholder="Komorebi">
      </div>
      <label for="prefix" data-i18n="prefix_label">邮箱前缀</label>
      <input id="prefix" name="prefix" autocomplete="username" placeholder="alice" required autofocus>
      <label for="domain" data-i18n="domain_label">邮箱域名</label>
      <select id="domain" name="domain" required>{domain_options}</select>
      <label for="password" data-i18n="password_label">账号密码</label>
      <input id="password" name="password" type="password" autocomplete="current-password" placeholder="请输入账号密码" required>
      <div class="register-only">
        <label for="invite_code" data-i18n="invite_label">邀请码</label>
        <input id="invite_code" name="invite_code" autocomplete="one-time-code" placeholder="INV-XXXXXXXXXX">
      </div>
      <button class="submit" type="submit" data-i18n="submit_login">登录</button>
    </form>
    <section class="forgot-panel">
      <p class="notice" data-i18n="forgot_notice">请联系管理员重置账号密码，或重新获取邀请码注册新账号。</p>
      <p class="footnote">管理员入口：<a href="/admin/login">/admin/login</a></p>
    </section>
  </main>
</div>
<script>
  const i18n = {{
    zh: {{
      intro_kicker:"欢迎回来", intro_title:"在星空下继续你的工作流", intro_lead:"登录已有账号，或使用管理员发放的邀请码完成注册。",
      brand_meta:"统一身份认证", title:"登录账号", lead:"请输入邮箱前缀和账号密码继续。", preview_notice:"登录已有账号，或使用管理员发放的邀请码注册。",
      tab_login:"登录", tab_register:"注册", tab_forgot:"找回", display_name:"显示名称", prefix_label:"邮箱前缀", domain_label:"邮箱域名", invite_label:"邀请码",
      password_label:"账号密码", submit_login:"登录", submit_register:"注册并继续", title_login:"登录账号", title_register:"注册账号",
      password_placeholder:"请输入账号密码", display_placeholder:"Komorebi", invite_placeholder:"INV-XXXXXXXXXX", forgot_notice:"请联系管理员重置账号密码，或重新获取邀请码注册新账号。"
    }},
    en: {{
      intro_kicker:"Welcome back", intro_title:"Continue your workflow beneath the stars", intro_lead:"Sign in with an existing account, or register with an invite code from an administrator.",
      brand_meta:"Unified identity", title:"Sign in", lead:"Enter your email prefix and account password to continue.", preview_notice:"Sign in with an existing account, or register with an administrator invite code.",
      tab_login:"Login", tab_register:"Register", tab_forgot:"Recover", display_name:"Display name", prefix_label:"Email prefix", domain_label:"Email domain", invite_label:"Invite code",
      password_label:"Account password", submit_login:"Login", submit_register:"Register and continue", title_login:"Sign in", title_register:"Create account",
      password_placeholder:"Enter account password", display_placeholder:"Komorebi", invite_placeholder:"INV-XXXXXXXXXX", forgot_notice:"Contact your administrator to reset your password or get a new invite code."
    }}
  }};
  const languageSelect = document.getElementById("languageSelect");
  const modeField = document.getElementById("modeField");
  const submitButton = document.querySelector(".login-form .submit");
  const titleNode = document.querySelector("h2");
  const passwordInput = document.getElementById("password");
  const displayNameInput = document.getElementById("display_name");
  const inviteInput = document.getElementById("invite_code");
  const setLanguage = (lang) => {{
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n]").forEach((node) => {{
      node.textContent = i18n[lang][node.dataset.i18n] || node.textContent;
    }});
    titleNode.textContent = document.body.dataset.mode === "register" ? i18n[lang].title_register : i18n[lang].title_login;
    submitButton.textContent = document.body.dataset.mode === "register" ? i18n[lang].submit_register : i18n[lang].submit_login;
    passwordInput.placeholder = i18n[lang].password_placeholder;
    displayNameInput.placeholder = i18n[lang].display_placeholder;
    inviteInput.placeholder = i18n[lang].invite_placeholder;
    localStorage.setItem("sso-language", lang);
  }};
  const setMode = (mode) => {{
    document.body.dataset.mode = mode;
    modeField.value = mode;
    inviteInput.required = mode === "register";
    document.querySelectorAll(".tab-button").forEach((button) => {{
      button.classList.toggle("active", button.dataset.modeTarget === mode);
    }});
    setLanguage(languageSelect.value);
  }};
  document.querySelectorAll(".tab-button").forEach((button) => {{
    button.addEventListener("click", () => setMode(button.dataset.modeTarget));
  }});
  languageSelect.value = localStorage.getItem("sso-language") || "zh";
  languageSelect.addEventListener("change", () => setLanguage(languageSelect.value));
  setMode("login");
</script>
</body>
</html>
"""


def render_user_console(email: str, profile: dict) -> str:
    display_name = html.escape(profile.get("name") or email)
    safe_email = html.escape(email)
    registered = fmt_time(profile.get("registered_at"))
    last_login = fmt_time(profile.get("last_login_at"))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Console | {html.escape(SERVICE_NAME)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: #101827;
      background:
        linear-gradient(180deg, rgba(255,255,255,.32), rgba(8,17,35,.34)),
        url("{html.escape(LOGIN_BACKGROUND_URL)}");
      background-position: center;
      background-size: cover;
      background-attachment: fixed;
    }}
    a {{ color: inherit; text-decoration: none; }}
    .topbar {{
      position: sticky;
      top: 0;
      min-height: 68px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 0 min(5vw, 56px);
      background: rgba(245,250,255,.62);
      border-bottom: 1px solid rgba(255,255,255,.45);
      backdrop-filter: blur(18px);
    }}
    .brand {{ display: inline-flex; align-items: center; gap: 10px; font-weight: 900; }}
    .mark {{ display: grid; place-items: center; width: 36px; height: 36px; color: #fff; background: #171c27; border-radius: 8px; }}
    .shell {{ width: min(1120px, calc(100% - 32px)); margin: 0 auto; padding: 46px 0 70px; }}
    .hero {{ color: #fff; text-shadow: 0 16px 44px rgba(8,24,44,.34); margin-bottom: 22px; }}
    .hero p {{ margin: 0 0 8px; font-weight: 800; opacity: .92; }}
    h1 {{ margin: 0; font-size: 44px; line-height: 1.1; letter-spacing: 0; }}
    .grid {{ display: grid; grid-template-columns: 1.1fr .9fr; gap: 18px; align-items: start; }}
    .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 18px; }}
    .panel, .stat {{
      border: 1px solid rgba(255,255,255,.68);
      border-radius: 8px;
      background: rgba(242,249,255,.70);
      box-shadow: 0 22px 70px rgba(8,24,44,.22);
      backdrop-filter: blur(20px);
    }}
    .stat {{ padding: 20px; min-height: 116px; }}
    .stat b {{ display: block; font-size: 24px; margin-bottom: 8px; }}
    .stat span {{ color: #5c6675; font-weight: 800; }}
    .panel {{ padding: 24px; }}
    h2 {{ margin: 0 0 16px; font-size: 22px; }}
    p {{ color: #536071; line-height: 1.65; }}
    code {{ padding: 4px 7px; border-radius: 6px; background: rgba(255,255,255,.72); overflow-wrap: anywhere; }}
    .list {{ display: grid; gap: 12px; }}
    .item {{ display: grid; grid-template-columns: 150px 1fr; gap: 16px; padding: 12px 0; border-bottom: 1px solid rgba(148,163,184,.28); }}
    .item strong {{ color: #334155; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .button {{ display: inline-flex; align-items: center; justify-content: center; min-height: 40px; padding: 0 16px; border-radius: 8px; color: #fff; background: #171c27; font-weight: 900; }}
    .button.secondary {{ color: #172033; background: rgba(255,255,255,.70); }}
    @media (max-width: 860px) {{
      .grid, .cards {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 34px; }}
      .topbar {{ align-items: flex-start; flex-direction: column; padding: 14px 16px; }}
      .item {{ grid-template-columns: 1fr; gap: 6px; }}
    }}
  </style>
</head>
<body>
  <header class="topbar">
    <a class="brand" href="/"><span class="mark">SSO</span><span>{html.escape(SERVICE_NAME)}</span></a>
    <div class="actions"><a class="button secondary" href="/admin/login">进入管理后台</a></div>
  </header>
  <main class="shell">
    <section class="hero">
      <p>个人控制台</p>
      <h1>欢迎回来，{display_name}</h1>
    </section>
    <section class="cards">
      <div class="stat"><b>已登录</b><span>账号状态</span></div>
      <div class="stat"><b>{registered}</b><span>注册时间</span></div>
      <div class="stat"><b>{last_login}</b><span>最后登录</span></div>
    </section>
    <section class="grid">
      <div class="panel">
        <h2>账号资料</h2>
        <div class="list">
          <div class="item"><strong>邮箱</strong><span><code>{safe_email}</code></span></div>
          <div class="item"><strong>显示名称</strong><span>{display_name}</span></div>
          <div class="item"><strong>登录方式</strong><span>账号密码 + OIDC 单点登录</span></div>
        </div>
      </div>
      <div class="panel">
        <h2>服务接入</h2>
        <p>下面是当前身份服务的标准 OIDC 端点，供 ChatGPT 或其他应用接入。</p>
        <p><code>/.well-known/openid-configuration</code></p>
        <p><code>/authorize</code> <code>/token</code> <code>/jwks.json</code></p>
        <div class="actions"><a class="button" href="/">返回首页</a><a class="button secondary" href="/.well-known/openid-configuration">查看 Discovery</a></div>
      </div>
    </section>
  </main>
</body>
</html>"""


def render_admin_login(error: str = "", redirect: str = "/console") -> str:
    error_block = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>管理员登录 | {html.escape(SERVICE_NAME)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: #111827;
      background: linear-gradient(90deg, rgba(8,17,35,.50), rgba(22,82,135,.12), rgba(255,214,218,.20)), url("{html.escape(LOGIN_BACKGROUND_URL)}");
      background-position: center;
      background-size: cover;
    }}
    main {{ width: min(430px, 100%); padding: 32px; border: 1px solid rgba(255,255,255,.68); border-radius: 8px; background: rgba(242,249,255,.70); box-shadow: 0 30px 86px rgba(8,24,44,.30); backdrop-filter: blur(22px); }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    p {{ margin: 0 0 20px; color: #5c6675; line-height: 1.6; }}
    label {{ display: block; margin: 14px 0 7px; font-weight: 900; }}
    input {{ width: 100%; min-height: 44px; padding: 10px 12px; border: 1px solid rgba(148,163,184,.58); border-radius: 8px; font: inherit; }}
    button, a.button {{ width: 100%; min-height: 46px; margin-top: 18px; border: 0; border-radius: 8px; color: #fff; background: #171c27; font: inherit; font-weight: 900; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; justify-content: center; }}
    a.text {{ display: inline-flex; margin-top: 14px; color: #172033; font-weight: 800; text-decoration: none; }}
    .error {{ margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; color: #8a241f; background: rgba(254,226,226,.84); border: 1px solid rgba(248,113,113,.30); }}
  </style>
</head>
<body>
  <main>
    <h1>管理员登录</h1>
    <p>登录后可生成邀请码、管理用户和设置注册策略。</p>
    {error_block}
    <form method="post" action="/admin/login">
      <input type="hidden" name="redirect" value="{html.escape(redirect)}">
      <label for="username">管理员账号</label>
      <input id="username" name="username" autocomplete="username" required autofocus>
      <label for="password">管理员密码</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">登录管理后台</button>
    </form>
    <a class="text" href="/auth/login?redirect=/console">返回用户登录</a>
  </main>
</body>
</html>"""


def render_admin_console() -> str:
    invite_rows = []
    for invite in sorted(invitations.values(), key=lambda item: int(item.get("created_at") or 0), reverse=True):
        code = html.escape(invite.get("code", ""))
        status = "启用" if invite.get("active", True) else "停用"
        note = html.escape(invite.get("note") or "-")
        uses = int(invite.get("uses") or 0)
        max_uses = int(invite.get("max_uses") or 1)
        expires = fmt_time(invite.get("expires_at"))
        created = fmt_time(invite.get("created_at"))
        action = "停用" if invite.get("active", True) else "启用"
        invite_rows.append(f"""
          <tr>
            <td><code>{code}</code></td>
            <td>{note}</td>
            <td>{uses}/{max_uses}</td>
            <td>{expires}</td>
            <td><span class="pill">{status}</span></td>
            <td>{created}</td>
            <td><form method="post" action="/admin/invites/{code}/toggle"><button class="ghost" type="submit">{action}</button></form></td>
          </tr>""")
    if not invite_rows:
        invite_rows.append('<tr><td colspan="7" class="empty">还没有邀请码，先生成一个。</td></tr>')

    user_rows = []
    for email, profile in sorted(profiles.items(), key=lambda item: int(item[1].get("registered_at") or 0), reverse=True):
        user_rows.append(f"""
          <tr>
            <td>{html.escape(email)}</td>
            <td>{html.escape(profile.get("name") or email)}</td>
            <td>{fmt_time(profile.get("registered_at"))}</td>
            <td>{fmt_time(profile.get("last_login_at"))}</td>
          </tr>""")
    if not user_rows:
        user_rows.append('<tr><td colspan="4" class="empty">暂无注册用户。</td></tr>')

    invite_checked = "checked" if app_settings.get("invite_required", True) else ""
    active_invites = sum(1 for item in invitations.values() if item.get("active", True) and invite_available(item.get("code", ""))[0])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>管理后台 | {html.escape(SERVICE_NAME)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; color: #111827; background: linear-gradient(180deg, rgba(255,255,255,.38), rgba(6,17,36,.36)), url("{html.escape(LOGIN_BACKGROUND_URL)}"); background-position: center; background-size: cover; background-attachment: fixed; }}
    .topbar {{ position: sticky; top: 0; z-index: 4; display: flex; justify-content: space-between; align-items: center; min-height: 68px; padding: 0 min(5vw, 56px); border-bottom: 1px solid rgba(255,255,255,.42); background: rgba(245,250,255,.62); backdrop-filter: blur(18px); }}
    .brand {{ display: flex; align-items: center; gap: 10px; font-weight: 900; }}
    .mark {{ display: grid; place-items: center; width: 36px; height: 36px; color: #fff; background: #171c27; border-radius: 8px; }}
    .layout {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 34px 0 64px; }}
    h1 {{ margin: 0 0 8px; color: #fff; font-size: 42px; text-shadow: 0 16px 44px rgba(8,24,44,.34); }}
    .lead {{ margin: 0 0 24px; color: rgba(255,255,255,.90); font-weight: 700; text-shadow: 0 10px 28px rgba(8,24,44,.30); }}
    .stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 18px; }}
    .stat, .panel {{ border: 1px solid rgba(255,255,255,.66); border-radius: 8px; background: rgba(242,249,255,.72); box-shadow: 0 22px 70px rgba(8,24,44,.22); backdrop-filter: blur(20px); }}
    .stat {{ padding: 20px; }}
    .stat b {{ display: block; font-size: 30px; }}
    .stat span {{ color: #5c6675; font-weight: 800; }}
    .grid {{ display: grid; grid-template-columns: 360px 1fr; gap: 18px; align-items: start; }}
    .panel {{ padding: 22px; overflow: hidden; }}
    h2 {{ margin: 0 0 16px; font-size: 22px; }}
    label {{ display: block; margin: 13px 0 7px; font-weight: 900; }}
    input {{ width: 100%; min-height: 42px; padding: 10px 12px; border: 1px solid rgba(148,163,184,.58); border-radius: 8px; font: inherit; }}
    .check {{ display: flex; align-items: center; gap: 10px; margin: 0; }}
    .check input {{ width: 18px; min-height: 18px; }}
    button, .link-button {{ min-height: 38px; padding: 0 14px; border: 0; border-radius: 8px; color: #fff; background: #171c27; font: inherit; font-weight: 900; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; justify-content: center; }}
    button.ghost {{ color: #172033; background: rgba(255,255,255,.72); border: 1px solid rgba(148,163,184,.38); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 12px 10px; border-bottom: 1px solid rgba(148,163,184,.28); text-align: left; vertical-align: middle; }}
    th {{ color: #516071; font-size: 12px; text-transform: uppercase; }}
    code {{ padding: 3px 6px; border-radius: 6px; background: rgba(255,255,255,.66); }}
    .pill {{ display: inline-flex; padding: 4px 8px; border-radius: 999px; background: rgba(22,119,255,.12); color: #1457b8; font-weight: 900; font-size: 12px; }}
    .empty {{ color: #64748b; text-align: center; }}
    .stack {{ display: grid; gap: 18px; }}
    .top-actions {{ display: flex; gap: 10px; align-items: center; }}
    @media (max-width: 900px) {{ .stats, .grid {{ grid-template-columns: 1fr; }} h1 {{ font-size: 34px; }} .topbar {{ align-items: flex-start; flex-direction: column; padding: 14px 16px; gap: 12px; }} }}
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand"><span class="mark">SSO</span><span>{html.escape(SERVICE_NAME)}</span></div>
    <div class="top-actions">
      <a class="link-button" href="/">首页</a>
      <form method="post" action="/admin/logout"><button class="ghost" type="submit">退出</button></form>
    </div>
  </header>
  <main class="layout">
    <h1>管理后台</h1>
    <p class="lead">管理用户、生成邀请码，并控制是否强制邀请码注册。</p>
    <section class="stats">
      <div class="stat"><b>{len(profiles)}</b><span>注册用户</span></div>
      <div class="stat"><b>{len(invitations)}</b><span>邀请码总数</span></div>
      <div class="stat"><b>{active_invites}</b><span>可用邀请码</span></div>
    </section>
    <section class="grid">
      <div class="stack">
        <section class="panel">
          <h2>注册策略</h2>
          <form method="post" action="/admin/settings">
            <label class="check"><input type="checkbox" name="invite_required" value="on" {invite_checked}> 注册时必须填写有效邀请码</label>
            <button style="width:100%; margin-top:16px" type="submit">保存设置</button>
          </form>
        </section>
        <section class="panel">
          <h2>生成邀请码</h2>
          <form method="post" action="/admin/invites">
            <label for="note">备注</label>
            <input id="note" name="note" placeholder="例如：6 月新用户">
            <label for="max_uses">可用次数</label>
            <input id="max_uses" name="max_uses" type="number" min="1" max="999" value="1">
            <label for="expires_days">有效天数</label>
            <input id="expires_days" name="expires_days" type="number" min="0" max="365" value="7">
            <button style="width:100%; margin-top:16px" type="submit">生成邀请码</button>
          </form>
        </section>
        <section class="panel">
          <h2>OIDC 信息</h2>
          <p><code>{html.escape(ISSUER or "not configured")}</code></p>
          <p><code>/.well-known/openid-configuration</code></p>
          <p><code>/authorize</code> <code>/token</code> <code>/jwks.json</code></p>
        </section>
      </div>
      <div class="stack">
        <section class="panel">
          <h2>邀请码</h2>
          <table>
            <thead><tr><th>邀请码</th><th>备注</th><th>使用</th><th>过期</th><th>状态</th><th>创建</th><th>操作</th></tr></thead>
            <tbody>{"".join(invite_rows)}</tbody>
          </table>
        </section>
        <section class="panel">
          <h2>用户</h2>
          <table>
            <thead><tr><th>邮箱</th><th>显示名</th><th>注册时间</th><th>最后登录</th></tr></thead>
            <tbody>{"".join(user_rows)}</tbody>
          </table>
        </section>
      </div>
    </section>
  </main>
</body>
</html>"""


def html_page(query: dict, error: Optional[str] = None, preview: bool = False) -> str:
    hidden = "\n".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(str(v))}">'
        for k, v in query.items()
    )
    error_block = f'<p class="error">{html.escape(error)}</p>' if error else ""
    domains = EMAIL_DOMAINS or [EMAIL_DOMAIN]
    domain_options = "\n".join(
        f'<option value="{html.escape(domain)}">{html.escape(domain)}</option>'
        for domain in domains
        if domain
    ) or '<option value="">not configured</option>'
    invite_required = bool(app_settings.get("invite_required", True))
    invite_label = "邀请码（注册时必填）" if invite_required else "邀请码（可选）"
    preview_alert = '<p class="notice" data-i18n="preview_notice">登录已有账号，或按当前注册策略使用邀请码注册。</p>' if preview else ""
    form_action = "/auth/login" if preview else "/authorize"
    background = html.escape(LOGIN_BACKGROUND_URL)
    service = html.escape(SERVICE_NAME)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Login | {service}</title>
  <style>
    :root {{ color-scheme: light; --ink:#111827; --muted:#5c6675; --blue:#1677ff; }}
    * {{ box-sizing: border-box; }}
    html, body {{ min-height: 100%; }}
    body {{ margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; color: var(--ink); background: linear-gradient(90deg, rgba(8,17,35,.52), rgba(22,82,135,.12), rgba(255,214,218,.20)), url("{background}"); background-position: center; background-size: cover; background-attachment: fixed; }}
    a {{ color: inherit; text-decoration: none; }}
    .page {{ min-height: 100vh; display: grid; grid-template-columns: minmax(0, 1fr) minmax(340px, 456px); gap: 56px; align-items: center; padding: 72px min(8vw, 96px); position: relative; overflow: hidden; }}
    .topbar {{ position: absolute; z-index: 2; top: 24px; left: min(8vw, 96px); right: min(8vw, 96px); display: flex; justify-content: space-between; align-items: center; gap: 16px; color: rgba(255,255,255,.94); }}
    .top-brand {{ display: inline-flex; align-items: center; gap: 10px; font-weight: 900; text-shadow: 0 2px 18px rgba(0,0,0,.28); }}
    .top-mark {{ display: grid; place-items: center; width: 36px; height: 36px; border-radius: 8px; color:#162033; background: rgba(255,255,255,.88); box-shadow: 0 12px 28px rgba(3,10,24,.20); }}
    .language-select {{ width: auto; min-height: 38px; padding: 0 34px 0 12px; color: #172033; background: rgba(255,255,255,.72); border: 1px solid rgba(255,255,255,.62); border-radius: 8px; font: inherit; font-size: 14px; font-weight: 800; backdrop-filter: blur(14px); }}
    .intro {{ position: relative; z-index: 1; max-width: 660px; color: #fff; text-shadow: 0 18px 45px rgba(6,13,28,.36); }}
    .intro p {{ margin: 0 0 14px; font-weight: 900; color: rgba(255,255,255,.90); }}
    .intro h1 {{ margin: 0; font-size: 54px; line-height: 1.08; letter-spacing: 0; }}
    .intro .lead {{ max-width: 520px; margin-top: 20px; color: rgba(255,255,255,.90); font-size: 17px; line-height: 1.7; font-weight: 700; }}
    .card {{ position: relative; z-index: 1; width: 100%; padding: 32px 36px; border: 1px solid rgba(255,255,255,.68); border-radius: 8px; background: rgba(242,249,255,.70); box-shadow: 0 30px 86px rgba(8,24,44,.30); backdrop-filter: blur(22px); }}
    .brand-row {{ display: flex; align-items: center; gap: 12px; margin-bottom: 22px; }}
    .brand-mark {{ display: grid; place-items: center; width: 42px; height: 42px; flex: 0 0 auto; color: #fff; background: #171c27; border-radius: 8px; font-size: 13px; font-weight: 900; }}
    .brand-name {{ margin: 0; font-size: 17px; font-weight: 900; }}
    .brand-meta {{ margin: 3px 0 0; color: #5c6675; font-size: 13px; font-weight: 700; }}
    h2 {{ margin: 0; font-size: 28px; line-height: 1.18; }}
    .card > .lead {{ margin: 10px 0 18px; color: var(--muted); font-size: 15px; line-height: 1.65; }}
    .tabs {{ display: grid; grid-template-columns: repeat(3, 1fr); margin: 18px 0 16px; border-bottom: 1px solid rgba(255,255,255,.72); }}
    .tab-button {{ min-height: 42px; margin: 0; color: #33455f; background: transparent; border: 0; border-bottom: 2px solid transparent; border-radius: 0; font: inherit; font-weight: 900; cursor: pointer; }}
    .tab-button.active {{ color: var(--blue); border-color: var(--blue); }}
    .register-only, .forgot-panel {{ display: none; }}
    body[data-mode="register"] .register-only {{ display: block; }}
    body[data-mode="forgot"] .login-form {{ display: none; }}
    body[data-mode="forgot"] .forgot-panel {{ display: block; }}
    label {{ display: block; margin: 14px 0 7px; color: #263241; font-size: 14px; font-weight: 900; }}
    input, select {{ width: 100%; min-height: 44px; padding: 10px 12px; color: #1f2937; background: rgba(255,255,255,.82); border: 1px solid rgba(148,163,184,.56); border-radius: 8px; font: inherit; font-size: 15px; outline: none; }}
    .submit, .admin-link {{ width: 100%; min-height: 46px; margin-top: 18px; border: 0; border-radius: 8px; color: #fff; background: #171c27; font: inherit; font-size: 15px; font-weight: 900; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }}
    .admin-link {{ color: #172033; background: rgba(255,255,255,.72); border: 1px solid rgba(148,163,184,.35); margin-top: 10px; }}
    .error, .notice {{ margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; font-size: 13px; line-height: 1.5; }}
    .error {{ color: #8a241f; background: rgba(254,226,226,.84); border: 1px solid rgba(248,113,113,.30); }}
    .notice {{ color: #24384f; background: rgba(239,246,255,.82); border: 1px solid rgba(96,165,250,.28); }}
    .footnote {{ margin: 16px 0 0; color: var(--muted); font-size: 13px; line-height: 1.55; }}
    @media (max-width: 820px) {{ .page {{ grid-template-columns: 1fr; gap: 28px; padding: 86px 16px 32px; }} .intro h1 {{ font-size: 38px; }} .card {{ padding: 26px; }} .topbar {{ left: 16px; right: 16px; top: 18px; }} }}
  </style>
</head>
<body data-mode="login">
<div class="page">
  <header class="topbar">
    <a class="top-brand" href="/"><span class="top-mark">SSO</span><span>{service}</span></a>
    <select class="language-select" id="languageSelect" aria-label="Language"><option value="zh">简体中文</option><option value="en">English</option></select>
  </header>
  <section class="intro" aria-hidden="true">
    <p data-i18n="intro_kicker">欢迎回来</p>
    <h1 data-i18n="intro_title">在星空下继续你的工作流</h1>
    <div class="lead" data-i18n="intro_lead">登录已有账号，或按管理员设置使用邀请码完成注册。</div>
  </section>
  <main class="card">
    <div class="brand-row"><div class="brand-mark">ID</div><div><p class="brand-name">{service}</p><p class="brand-meta" data-i18n="brand_meta">统一身份认证</p></div></div>
    <h2 data-i18n="title">登录账号</h2>
    <p class="lead" data-i18n="lead">请输入邮箱前缀和账号密码继续。</p>
    {preview_alert}
    {error_block}
    <nav class="tabs" aria-label="Account actions">
      <button class="tab-button active" type="button" data-mode-target="login" data-i18n="tab_login">登录</button>
      <button class="tab-button" type="button" data-mode-target="register" data-i18n="tab_register">注册</button>
      <button class="tab-button" type="button" data-mode-target="forgot" data-i18n="tab_forgot">找回</button>
    </nav>
    <form class="login-form" method="post" action="{form_action}">
      {hidden}
      <input type="hidden" id="modeField" name="mode" value="login">
      <div class="register-only"><label for="display_name" data-i18n="display_name">显示名称</label><input id="display_name" name="display_name" autocomplete="name" placeholder="Komorebi"></div>
      <label for="prefix" data-i18n="prefix_label">邮箱前缀</label>
      <input id="prefix" name="prefix" autocomplete="username" placeholder="alice" required autofocus>
      <label for="domain" data-i18n="domain_label">邮箱域名</label>
      <select id="domain" name="domain" required>{domain_options}</select>
      <label for="password" data-i18n="password_label">账号密码</label>
      <input id="password" name="password" type="password" autocomplete="current-password" placeholder="请输入账号密码" required>
      <div class="register-only"><label for="invite_code" id="inviteLabel" data-i18n="invite_label">{invite_label}</label><input id="invite_code" name="invite_code" autocomplete="one-time-code" placeholder="INV-XXXXXXXXXX"></div>
      <button class="submit" type="submit" data-i18n="submit_login">登录</button>
    </form>
    <a class="admin-link" href="/admin/login?redirect=/console" data-i18n="admin_login">进入管理后台</a>
    <section class="forgot-panel"><p class="notice" data-i18n="forgot_notice">请联系管理员重置账号密码，或按当前注册策略重新注册。</p></section>
  </main>
</div>
<script>
  const inviteRequired = {str(invite_required).lower()};
  const i18n = {{
    zh: {{
      intro_kicker:"欢迎回来", intro_title:"在星空下继续你的工作流", intro_lead:"登录已有账号，或按管理员设置使用邀请码完成注册。",
      brand_meta:"统一身份认证", title:"登录账号", lead:"请输入邮箱前缀和账号密码继续。", preview_notice:"登录已有账号，或按当前注册策略使用邀请码注册。",
      tab_login:"登录", tab_register:"注册", tab_forgot:"找回", display_name:"显示名称", prefix_label:"邮箱前缀", domain_label:"邮箱域名",
      invite_label_required:"邀请码（注册时必填）", invite_label_optional:"邀请码（可选）", password_label:"账号密码", submit_login:"登录", submit_register:"注册并继续",
      title_login:"登录账号", title_register:"注册账号", password_placeholder:"请输入账号密码", forgot_notice:"请联系管理员重置账号密码，或按当前注册策略重新注册。", admin_login:"进入管理后台"
    }},
    en: {{
      intro_kicker:"Welcome back", intro_title:"Continue your workflow beneath the stars", intro_lead:"Sign in, or register according to the administrator's invite policy.",
      brand_meta:"Unified identity", title:"Sign in", lead:"Enter your email prefix and account password to continue.", preview_notice:"Sign in, or register according to the current invite policy.",
      tab_login:"Login", tab_register:"Register", tab_forgot:"Recover", display_name:"Display name", prefix_label:"Email prefix", domain_label:"Email domain",
      invite_label_required:"Invite code (required for registration)", invite_label_optional:"Invite code (optional)", password_label:"Account password", submit_login:"Login", submit_register:"Register and continue",
      title_login:"Sign in", title_register:"Create account", password_placeholder:"Enter account password", forgot_notice:"Contact your administrator to reset your password or register again according to policy.", admin_login:"Admin console"
    }}
  }};
  const languageSelect = document.getElementById("languageSelect");
  const modeField = document.getElementById("modeField");
  const submitButton = document.querySelector(".login-form .submit");
  const titleNode = document.querySelector("h2");
  const passwordInput = document.getElementById("password");
  const inviteInput = document.getElementById("invite_code");
  const inviteLabelNode = document.getElementById("inviteLabel");
  const setLanguage = (lang) => {{
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n]").forEach((node) => {{
      const key = node.dataset.i18n;
      node.textContent = i18n[lang][key] || node.textContent;
    }});
    titleNode.textContent = document.body.dataset.mode === "register" ? i18n[lang].title_register : i18n[lang].title_login;
    submitButton.textContent = document.body.dataset.mode === "register" ? i18n[lang].submit_register : i18n[lang].submit_login;
    passwordInput.placeholder = i18n[lang].password_placeholder;
    inviteLabelNode.textContent = inviteRequired ? i18n[lang].invite_label_required : i18n[lang].invite_label_optional;
    localStorage.setItem("sso-language", lang);
  }};
  const setMode = (mode) => {{
    document.body.dataset.mode = mode;
    modeField.value = mode;
    inviteInput.required = mode === "register" && inviteRequired;
    document.querySelectorAll(".tab-button").forEach((button) => button.classList.toggle("active", button.dataset.modeTarget === mode));
    setLanguage(languageSelect.value);
  }};
  document.querySelectorAll(".tab-button").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.modeTarget)));
  languageSelect.value = localStorage.getItem("sso-language") || "zh";
  languageSelect.addEventListener("change", () => setLanguage(languageSelect.value));
  setMode("login");
</script>
</body>
</html>"""


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
        "name": record.get("name") or email,
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
