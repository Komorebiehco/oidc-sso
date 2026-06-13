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
DEFAULT_BACKGROUND_URL = "https://images6.alphacoders.com/112/1125678.png"
LOGIN_BACKGROUND_URL = os.environ.get("LOGIN_BACKGROUND_URL", DEFAULT_BACKGROUND_URL).strip() or DEFAULT_BACKGROUND_URL
SERVICE_NAME = os.environ.get("SERVICE_NAME", "Komorebi SSO").strip() or "Komorebi SSO"

PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
KEY_PATH = DATA_DIR / "private_key.pem"
KID_PATH = DATA_DIR / "kid.txt"

codes: Dict[str, dict] = {}
profiles: Dict[str, dict] = {}


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
):
    target = safe_local_redirect(redirect)
    query = {"redirect": target}
    if not secrets.compare_digest(password, SHARED_PASSWORD):
        return html_page(query, "Invalid shared password.", preview=True)
    try:
        email = prefix_to_email(prefix, domain)
    except ValueError as exc:
        return html_page(query, str(exc), preview=True)

    normalized_prefix = prefix.strip().lower()
    if mode == "register":
        profiles[email] = {
            "name": display_name.strip() or email,
            "prefix": normalized_prefix,
            "registered_at": int(time.time()),
        }

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
    email = request.cookies.get("sso_user", "")
    profile = profiles.get(email, {})
    if not email:
        return RedirectResponse("/auth/login?redirect=/console", status_code=303)
    display_name = profile.get("name") or email
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
      background-image: linear-gradient(90deg, rgba(7,16,31,.40), rgba(24,65,103,.08)), url("{html.escape(LOGIN_BACKGROUND_URL)}");
      background-position: center;
      background-size: cover;
    }}
    main {{
      width: min(520px, 100%);
      padding: 34px;
      background: rgba(238, 248, 255, .62);
      border: 1px solid rgba(255,255,255,.72);
      border-radius: 8px;
      box-shadow: 0 28px 80px rgba(8,24,44,.26);
      backdrop-filter: blur(18px);
    }}
    h1 {{ margin: 0 0 12px; font-size: 28px; }}
    p {{ margin: 0 0 18px; color: #526071; line-height: 1.65; }}
    code {{ padding: 3px 6px; background: rgba(255,255,255,.64); border-radius: 6px; }}
    a {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
      padding: 0 16px;
      color: #fff;
      background: #24272f;
      border-radius: 8px;
      text-decoration: none;
      font-weight: 800;
    }}
  </style>
</head>
<body>
  <main>
    <h1>控制台</h1>
    <p>已登录为 <code>{html.escape(display_name)}</code></p>
    <p>OIDC Discovery: <code>/.well-known/openid-configuration</code></p>
    <a href="/">返回首页</a>
  </main>
</body>
</html>"""
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
    mode: str = Form("login"),
    display_name: str = Form(""),
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

    normalized_prefix = prefix.strip().lower()
    if mode == "register":
        profiles[email] = {
            "name": display_name.strip() or email,
            "prefix": normalized_prefix,
            "registered_at": int(time.time()),
        }
    profile = profiles.get(email, {})

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
    domain_hint = ", ".join(domain for domain in domains if domain) or "not configured"
    preview_alert = (
        '<p class="notice" data-i18n="preview_notice">使用共享密码登录或注册，然后进入控制台。</p>'
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
        linear-gradient(90deg, rgba(8,17,35,.48), rgba(22,82,135,.10) 48%, rgba(255,214,218,.22)),
        linear-gradient(180deg, rgba(255,255,255,.18), rgba(8,17,35,.30)),
        url("{background}");
      background-position: center;
      background-size: cover;
      background-attachment: fixed;
    }}
    a {{ color: inherit; text-decoration: none; }}
    .page {{ min-height: 100vh; display: grid; grid-template-columns: minmax(0, 1fr) minmax(340px, 456px); gap: 56px; align-items: center; padding: 72px min(8vw, 96px); position: relative; overflow: hidden; }}
    .page::before {{ content:""; position:absolute; inset:0; pointer-events:none; background: linear-gradient(116deg, transparent 0 52%, rgba(255,255,255,.28) 52.2%, transparent 53.4%); }}
    .topbar {{ position: absolute; z-index: 2; top: 24px; left: min(8vw, 96px); right: min(8vw, 96px); display: flex; justify-content: space-between; align-items: center; gap: 16px; color: rgba(255,255,255,.92); }}
    .top-brand {{ display: inline-flex; align-items: center; gap: 10px; font-weight: 900; text-shadow: 0 2px 18px rgba(0,0,0,.28); }}
    .top-mark {{ display: grid; place-items: center; width: 36px; height: 36px; border-radius: 8px; color:#162033; background: rgba(255,255,255,.86); box-shadow: 0 12px 28px rgba(3,10,24,.20); }}
    .language-select {{ width: auto; min-height: 38px; padding: 0 34px 0 12px; color: #172033; background: rgba(255,255,255,.70); border: 1px solid rgba(255,255,255,.58); border-radius: 8px; font: inherit; font-size: 14px; font-weight: 800; backdrop-filter: blur(14px); }}
    .intro {{ position: relative; z-index: 1; max-width: 620px; color: #fff; text-shadow: 0 18px 45px rgba(6,13,28,.36); }}
    .intro p {{ margin: 0 0 14px; font-weight: 900; color: rgba(255,255,255,.88); }}
    .intro h1 {{ margin: 0; font-size: 54px; line-height: 1.08; letter-spacing: 0; }}
    .intro .lead {{ max-width: 500px; margin-top: 20px; color: rgba(255,255,255,.88); font-size: 17px; line-height: 1.7; font-weight: 650; }}
    .card {{ position: relative; z-index: 1; width: 100%; padding: 32px 36px; border: 1px solid rgba(255,255,255,.66); border-radius: 8px; background: rgba(242,249,255,.62); box-shadow: 0 30px 86px rgba(8,24,44,.30), inset 0 1px 0 rgba(255,255,255,.58); backdrop-filter: blur(22px); }}
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
    input, select {{ width: 100%; min-height: 44px; padding: 10px 12px; color: #1f2937; background: rgba(255,255,255,.76); border: 1px solid rgba(148,163,184,.56); border-radius: 8px; font: inherit; font-size: 15px; outline: none; transition: border-color .18s ease, box-shadow .18s ease, background .18s ease; }}
    input:focus, select:focus {{ background: rgba(255,255,255,.94); border-color: #3b82f6; box-shadow: 0 0 0 4px rgba(59,130,246,.16); }}
    .submit {{ width: 100%; min-height: 46px; margin-top: 18px; border: 0; border-radius: 8px; color: #fff; background: #171c27; font: inherit; font-size: 15px; font-weight: 900; cursor: pointer; box-shadow: 0 16px 34px rgba(17,24,39,.26); transition: transform .16s ease, opacity .16s ease, box-shadow .16s ease; }}
    .submit:hover {{ transform: translateY(-1px); opacity: .95; box-shadow: 0 20px 42px rgba(17,24,39,.30); }}
    .error, .notice {{ margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; font-size: 13px; line-height: 1.5; }}
    .error {{ color: #8a241f; background: rgba(254,226,226,.84); border: 1px solid rgba(248,113,113,.30); }}
    .notice {{ color: #24384f; background: rgba(239,246,255,.80); border: 1px solid rgba(96,165,250,.28); }}
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
    <div class="lead" data-i18n="intro_lead">登录后将安全返回目标服务，整个流程保持简洁、快速且可信。</div>
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
    <p class="lead" data-i18n="lead">使用允许的邮箱前缀和共享密码继续。</p>
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
      <label for="password" data-i18n="password_label">共享密码</label>
      <input id="password" name="password" type="password" autocomplete="current-password" placeholder="请输入共享密码" required>
      <button class="submit" type="submit" data-i18n="submit_login">登录</button>
    </form>
    <section class="forgot-panel">
      <p class="notice" data-i18n="forgot_notice">请联系管理员重置共享密码，或确认允许登录的邮箱前缀。</p>
      <p class="footnote">Discovery: <code>/.well-known/openid-configuration</code></p>
    </section>
    <p class="footnote"><span data-i18n="allowed_domains">允许域名</span>: <code>{html.escape(domain_hint)}</code></p>
  </main>
</div>
<script>
  const i18n = {{
    zh: {{
      intro_kicker:"欢迎回来", intro_title:"在星空下继续你的工作流", intro_lead:"登录后将安全返回目标服务，整个流程保持简洁、快速且可信。",
      brand_meta:"统一身份认证", title:"登录账号", lead:"使用允许的邮箱前缀和共享密码继续。", preview_notice:"使用共享密码登录或注册，然后进入控制台。",
      tab_login:"登录", tab_register:"注册", tab_forgot:"找回", display_name:"显示名称", prefix_label:"邮箱前缀", domain_label:"邮箱域名",
      password_label:"共享密码", submit_login:"登录", submit_register:"注册并继续", title_login:"登录账号", title_register:"注册账号",
      password_placeholder:"请输入共享密码", display_placeholder:"Komorebi", forgot_notice:"请联系管理员重置共享密码，或确认允许登录的邮箱前缀。", allowed_domains:"允许域名"
    }},
    en: {{
      intro_kicker:"Welcome back", intro_title:"Continue your workflow beneath the stars", intro_lead:"After signing in, you will return to the target service through a simple, trusted flow.",
      brand_meta:"Unified identity", title:"Sign in", lead:"Use an allowed email prefix and the shared password to continue.", preview_notice:"Use the shared password to login or register, then enter the console.",
      tab_login:"Login", tab_register:"Register", tab_forgot:"Recover", display_name:"Display name", prefix_label:"Email prefix", domain_label:"Email domain",
      password_label:"Shared password", submit_login:"Login", submit_register:"Register and continue", title_login:"Sign in", title_register:"Create account",
      password_placeholder:"Enter shared password", display_placeholder:"Komorebi", forgot_notice:"Contact your administrator to reset the shared password or confirm allowed prefixes.", allowed_domains:"Allowed domains"
    }}
  }};
  const languageSelect = document.getElementById("languageSelect");
  const modeField = document.getElementById("modeField");
  const submitButton = document.querySelector(".login-form .submit");
  const titleNode = document.querySelector("h2");
  const passwordInput = document.getElementById("password");
  const displayNameInput = document.getElementById("display_name");
  const setLanguage = (lang) => {{
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n]").forEach((node) => {{
      node.textContent = i18n[lang][node.dataset.i18n] || node.textContent;
    }});
    titleNode.textContent = document.body.dataset.mode === "register" ? i18n[lang].title_register : i18n[lang].title_login;
    submitButton.textContent = document.body.dataset.mode === "register" ? i18n[lang].submit_register : i18n[lang].submit_login;
    passwordInput.placeholder = i18n[lang].password_placeholder;
    displayNameInput.placeholder = i18n[lang].display_placeholder;
    localStorage.setItem("sso-language", lang);
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
  languageSelect.value = localStorage.getItem("sso-language") || "zh";
  languageSelect.addEventListener("change", () => setLanguage(languageSelect.value));
  setLanguage(languageSelect.value);
</script>
</body>
</html>
"""


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
