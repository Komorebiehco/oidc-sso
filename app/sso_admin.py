from __future__ import annotations

import contextvars
import html
import re
import secrets
from collections.abc import MutableMapping
from urllib.parse import urlencode

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse


SIMPLE_SEED_CONFIGS = [
    ("edu.168.edu.kg", "edu168", "168", False),
    ("edu.10086.it.com", "edu10086", "10086", True),
    ("edu.234.it.com", "edu234", "edu234", True),
    ("edu.gmail.168.edu.kg", "gmail168", "gmail168", True),
    ("edu.gmail.stripe.edu.kg", "gmailstripe", "gmailstripe", True),
    ("edu.google.168.edu.kg", "google168", "google168", True),
    ("edu.a.gpt8.store", "gpt8", "gpt8", True),
    ("edu.a.stripe.edu.kg", "stripe-a", "stripe-a", True),
    ("edu.yahoo.234.it.com", "yahoo234", "yahoo234", True),
]

SECTIONS = {
    "home": "首页",
    "security": "注册安全",
    "txt": "TXT 验证",
    "list": "SSO 列表",
    "edit": "编辑当前",
    "batch": "批量基础设置",
    "add": "新增 SSO",
    "cards": "生成卡密",
    "latest_card": "最新卡密",
    "users": "管理用户",
    "emails": "邮箱记录",
}

STATE = {
    "configs": {},
    "settings": {},
    "email_records": [],
}

INSTALLED = False
RUNTIME_INSTALLED = False
ACTIVE_WORKSPACE_ID = contextvars.ContextVar("active_sso_workspace_id", default="")
STATE_MISSING = object()

ADMIN_CSS = """
:root {
  color-scheme: light;
  --bg: #f7f8fa;
  --panel: #ffffff;
  --panel-soft: #f3f5f7;
  --line: #d9dde3;
  --text: #0f1720;
  --muted: #5c6470;
  --green: #d9fce6;
  --green-text: #087443;
  --gray: #edf0f3;
  --blue: #256fcb;
  --danger: #b42318;
}
* { box-sizing: border-box; }
html, body { min-height: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 15px;
}
a { color: inherit; text-decoration: none; }
button, input, textarea, select { font: inherit; }
.admin-shell {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 22px;
  width: min(1320px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 32px 0 44px;
}
.sidebar {
  position: sticky;
  top: 22px;
  align-self: start;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f7f8fa;
}
.brand-row { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
.brand-mark {
  display: grid;
  place-items: center;
  width: 56px;
  height: 56px;
  border-radius: 8px;
  background: #111;
  color: #fff;
  font-size: 20px;
  font-weight: 900;
}
.brand-title { margin: 0; font-size: 20px; font-weight: 900; }
.brand-sub { margin: 4px 0 0; color: var(--muted); font-weight: 700; }
.side-nav { display: grid; gap: 9px; }
.side-item, .logout-button {
  display: flex;
  align-items: center;
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f8fafc;
  font-size: 16px;
  font-weight: 900;
}
.side-item.active { border-color: #b8c2cf; background: #fff; }
.logout-button {
  justify-content: center;
  width: 100%;
  margin-top: 20px;
  border: 0;
  background: #ece7df;
  color: #111;
  cursor: pointer;
}
.content { min-width: 0; }
.page-title { margin: -6px 0 6px; font-size: 34px; line-height: 1.12; font-weight: 950; }
.lead { margin: 0 0 18px; color: var(--muted); font-size: 18px; line-height: 1.45; }
.notice {
  margin: 0 0 14px;
  padding: 10px 12px;
  border: 1px solid #c7dcff;
  border-radius: 8px;
  background: #eef6ff;
  color: #24496f;
  font-weight: 800;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}
.grid { display: grid; gap: 12px; }
.grid.two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.panel, .sso-row {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}
.panel { padding: 16px; }
.sso-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  align-items: center;
  min-height: 74px;
  padding: 14px 16px;
}
.sso-name { margin: 0 0 4px; font-size: 20px; line-height: 1.18; font-weight: 950; overflow-wrap: anywhere; }
.sso-meta { color: var(--muted); font-size: 15px; line-height: 1.45; overflow-wrap: anywhere; }
.pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 56px;
  min-height: 34px;
  padding: 0 12px;
  border-radius: 8px;
  background: var(--green);
  color: var(--green-text);
  font-weight: 900;
}
.pill.off { background: var(--gray); color: #45505d; }
.pill.warn { background: #fff3c4; color: #7a4b00; }
.row-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 36px;
  padding: 0 13px;
  border: 1px solid #1f2937;
  border-radius: 8px;
  background: #1f2937;
  color: #fff;
  font-weight: 900;
  cursor: pointer;
}
.btn.secondary { border-color: var(--line); background: #fff; color: #172033; }
.btn.danger { border-color: #f5b1aa; background: #fff1f0; color: var(--danger); }
.btn.soft { border-color: #c9dcff; background: #ecf5ff; color: var(--blue); }
form { margin: 0; }
label { display: block; margin: 0 0 7px; color: #1f2937; font-weight: 900; }
input, textarea, select {
  width: 100%;
  min-height: 40px;
  padding: 9px 11px;
  border: 1px solid #cfd6df;
  border-radius: 8px;
  background: #fff;
  color: #111827;
}
textarea { min-height: 82px; resize: vertical; }
.field { display: grid; gap: 7px; margin-bottom: 14px; }
.field-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.muted { color: var(--muted); line-height: 1.55; }
.code-line {
  display: block;
  width: 100%;
  padding: 9px 11px;
  border: 1px solid #d7dee8;
  border-radius: 8px;
  background: #f8fafc;
  color: #172033;
  overflow-wrap: anywhere;
}
.table-wrap { width: 100%; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; min-width: 760px; }
th, td { padding: 10px 9px; border-bottom: 1px solid #e6e9ee; text-align: left; vertical-align: top; }
th { color: #334155; font-size: 12px; text-transform: uppercase; }
td.row-actions {
  display: table-cell;
  min-width: 132px;
  white-space: nowrap;
}
td.row-actions form, td.row-actions a {
  display: inline-flex;
  margin: 0 4px 6px 0;
  vertical-align: top;
}
td code { overflow-wrap: anywhere; }
.empty { padding: 24px; color: var(--muted); text-align: center; }
.stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }
.stat { padding: 15px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
.stat b { display: block; margin-bottom: 6px; font-size: 24px; }
.check-cell { width: 34px; }
input[type="checkbox"] { width: 16px; min-height: 16px; padding: 0; }
@media (max-width: 920px) {
  .admin-shell { grid-template-columns: 1fr; width: min(100% - 24px, 720px); padding-top: 18px; }
  .sidebar { position: static; }
  .page-title { font-size: 30px; }
  .lead { font-size: 16px; }
  .grid.two, .field-row, .stat-grid { grid-template-columns: 1fr; }
  .sso-row { grid-template-columns: 1fr; }
  .row-actions { justify-content: flex-start; }
}
"""

ADMIN_JS = """
document.querySelectorAll("[data-check-all]").forEach((box) => {
  box.addEventListener("change", () => {
    document.querySelectorAll(box.dataset.checkAll).forEach((item) => { item.checked = box.checked; });
  });
});
"""


def _safe(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or f"sso-{secrets.token_hex(3)}"


def _storage_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(value or "default")) or "default"


def _now(ns: dict) -> int:
    return int(ns["now_ts"]())


def _backend(ns: dict):
    return ns["state_backend"]()


def _workspace_storage_key(workspace_id: str, name: str) -> str:
    return f"workspace_{_storage_slug(workspace_id)}_{name}"


def _sorted_workspace_configs() -> list[dict]:
    return _sorted_configs()


def _default_workspace_id(ns: dict) -> str:
    client_id = str(ns.get("CLIENT_ID") or "")
    domains = {str(domain).strip().lower() for domain in ns.get("EMAIL_DOMAINS", []) if str(domain).strip()}
    configs = _sorted_workspace_configs()
    for config in configs:
        if client_id and str(config.get("client_id") or "") == client_id:
            return str(config.get("id") or config.get("slug") or "default")
    for config in configs:
        if str(config.get("domain") or "").strip().lower() in domains:
            return str(config.get("id") or config.get("slug") or "default")
    if configs:
        return str(configs[0].get("id") or configs[0].get("slug") or "default")
    return "default"


def current_workspace_id(ns: dict | None = None) -> str:
    current = ACTIVE_WORKSPACE_ID.get()
    if current:
        return current
    if ns is not None:
        return _default_workspace_id(ns)
    return "default"


def _workspace_config(workspace_id: str) -> dict | None:
    return STATE["configs"].get(workspace_id)


def _select_workspace(ns: dict, workspace_id: str = "") -> str:
    load_state(ns)
    requested = str(workspace_id or "").strip()
    if requested and requested in STATE["configs"]:
        return requested
    return _default_workspace_id(ns)


def _workspace_domains(ns: dict, workspace_id: str = "") -> list[str]:
    workspace_id = workspace_id or current_workspace_id(ns)
    config = _workspace_config(workspace_id)
    domains = []
    if config:
        raw_domains = config.get("domains")
        if isinstance(raw_domains, list):
            domains.extend(str(domain).strip().lower() for domain in raw_domains if str(domain).strip())
        domain = str(config.get("domain") or "").strip().lower()
        if domain:
            domains.append(domain)
    if not domains:
        domains.extend(str(domain).strip().lower() for domain in ns.get("EMAIL_DOMAINS", []) if str(domain).strip())
    seen = set()
    return [domain for domain in domains if not (domain in seen or seen.add(domain))]


class WorkspaceMapping(MutableMapping):
    def __init__(self, ns: dict, name: str, legacy: dict, default_factory):
        self.ns = ns
        self.name = name
        self.legacy = dict(legacy or {})
        self.default_factory = default_factory
        self.cache: dict[str, dict] = {}

    def _workspace_id(self) -> str:
        return current_workspace_id(self.ns)

    def _data_for(self, workspace_id: str) -> dict:
        workspace_id = workspace_id or _default_workspace_id(self.ns)
        if workspace_id not in self.cache:
            key = _workspace_storage_key(workspace_id, self.name)
            value = _backend(self.ns).load_json(key, STATE_MISSING)
            if value is STATE_MISSING:
                value = dict(self.legacy) if workspace_id == _default_workspace_id(self.ns) else self.default_factory()
                _backend(self.ns).save_json(key, value)
            if not isinstance(value, dict):
                value = self.default_factory()
            self.cache[workspace_id] = value
        return self.cache[workspace_id]

    def _data(self) -> dict:
        return self._data_for(self._workspace_id())

    def save_current(self) -> None:
        workspace_id = self._workspace_id()
        _backend(self.ns).save_json(_workspace_storage_key(workspace_id, self.name), self._data_for(workspace_id))

    def __getitem__(self, key):
        return self._data()[key]

    def __setitem__(self, key, value):
        self._data()[key] = value

    def __delitem__(self, key):
        del self._data()[key]

    def __iter__(self):
        return iter(self._data())

    def __len__(self):
        return len(self._data())

    def __contains__(self, key):
        return key in self._data()

    def get(self, key, default=None):
        return self._data().get(key, default)

    def pop(self, key, default=None):
        return self._data().pop(key, default)

    def clear(self):
        self._data().clear()

    def update(self, *args, **kwargs):
        self._data().update(*args, **kwargs)


def _default_provider_url(ns: dict) -> str:
    issuer = str(ns.get("ISSUER") or "").strip().rstrip("/")
    return issuer or "https://168.edu.kg"


def _default_settings(ns: dict) -> dict:
    provider = _default_provider_url(ns)
    return {
        "public_provider_url": provider,
        "issuer_base": provider,
        "redirect_template": f"{provider}/{{slug}}/callback",
        "provider_mode": "公共 Provider",
    }


def _make_txt_token() -> str:
    return "sso-verify=" + secrets.token_urlsafe(18).replace("_", "").replace("-", "")


def _new_config(ns: dict, domain: str, slug: str, path: str, enabled: bool = True) -> dict:
    clean_slug = _slugify(slug or domain)
    provider = str(STATE["settings"].get("public_provider_url") or _default_provider_url(ns)).rstrip("/")
    now = _now(ns)
    return {
        "id": clean_slug,
        "domain": domain.strip().lower(),
        "slug": clean_slug,
        "base_url": f"{provider}/{path.strip('/') or clean_slug}",
        "provider_url": provider,
        "issuer": str(STATE["settings"].get("issuer_base") or provider).rstrip("/"),
        "client_id": clean_slug,
        "client_secret": "",
        "redirect_uri": str(STATE["settings"].get("redirect_template") or f"{provider}/{{slug}}/callback").replace(
            "{slug}", clean_slug
        ),
        "application_login_url": "",
        "enabled": bool(enabled),
        "txt_name": f"_sso.{domain.strip().lower()}",
        "txt_value": _make_txt_token(),
        "txt_verified": False,
        "txt_last_checked_at": 0,
        "txt_last_error": "",
        "notes": "",
        "created_at": now,
        "updated_at": now,
    }


def _seed_configs(ns: dict) -> dict:
    domains = [str(domain).strip().lower() for domain in ns.get("EMAIL_DOMAINS", []) if str(domain).strip()]
    if domains:
        return {
            _slugify(domain): _new_config(ns, domain, _slugify(domain), _slugify(domain), True)
            for domain in domains
        }
    return {
        slug: _new_config(ns, domain, slug, path, enabled)
        for domain, slug, path, enabled in SIMPLE_SEED_CONFIGS
    }


def load_state(ns: dict, *, seed: bool = True) -> None:
    settings = _backend(ns).load_json("sso_admin_settings", _default_settings(ns))
    merged_settings = _default_settings(ns)
    if isinstance(settings, dict):
        merged_settings.update(settings)
    STATE["settings"] = merged_settings

    configs = _backend(ns).load_json("sso_configs", {})
    if not isinstance(configs, dict):
        configs = {}
    if seed and not configs:
        configs = _seed_configs(ns)
        _backend(ns).save_json("sso_configs", configs)
    STATE["configs"] = configs

    email_records = _backend(ns).load_json("sso_email_records", [])
    normalized_records = []
    records_changed = False
    if isinstance(email_records, list):
        for item in email_records:
            if not isinstance(item, dict):
                continue
            record = dict(item)
            if not record.get("id"):
                record["id"] = f"mail-{secrets.token_hex(6)}"
                records_changed = True
            record["id"] = str(record.get("id"))
            normalized_records.append(record)
    STATE["email_records"] = normalized_records
    if records_changed:
        _backend(ns).save_json("sso_email_records", STATE["email_records"])


def save_settings(ns: dict) -> None:
    _backend(ns).save_json("sso_admin_settings", STATE["settings"])


def save_configs(ns: dict) -> None:
    _backend(ns).save_json("sso_configs", STATE["configs"])


def save_email_records(ns: dict) -> None:
    _backend(ns).save_json("sso_email_records", STATE["email_records"])


def _admin_redirect() -> RedirectResponse:
    return RedirectResponse("/admin/login?redirect=/admin/sso", status_code=303)


def _redirect(section: str = "home", **params: str) -> RedirectResponse:
    query = {
        "section": section,
        "workspace": current_workspace_id(),
        **{key: value for key, value in params.items() if value},
    }
    return RedirectResponse("/admin/sso?" + urlencode(query), status_code=303)


def _workspace_url(section: str = "home", **params: str) -> str:
    query = {
        "section": section,
        "workspace": current_workspace_id(),
        **{key: value for key, value in params.items() if value},
    }
    return "/admin/sso?" + urlencode(query)


def _workspace_selector(ns: dict, section: str, current_id: str = "") -> str:
    current_workspace = current_workspace_id(ns)
    options = []
    for config in _sorted_workspace_configs():
        workspace_id = str(config.get("id") or config.get("slug") or "")
        if not workspace_id:
            continue
        selected = "selected" if workspace_id == current_workspace else ""
        label = config.get("domain") or config.get("slug") or workspace_id
        options.append(f'<option value="{_safe(workspace_id)}" {selected}>{_safe(label)}</option>')
    if not options:
        options.append('<option value="default">default</option>')
    return f"""
    <section class="panel workspace-switch">
      <form method="get" action="/admin/sso" class="field-row">
        <input type="hidden" name="section" value="{_safe(section)}">
        <input type="hidden" name="current" value="{_safe(current_id)}">
        <div class="field">
          <label for="workspace">当前工作空间</label>
          <select id="workspace" name="workspace">{''.join(options)}</select>
        </div>
        <div class="field">
          <label>&nbsp;</label>
          <button class="btn secondary" type="submit">切换工作空间</button>
        </div>
      </form>
    </section>
    """


def _status_pill(config: dict) -> str:
    if config.get("enabled", True):
        return '<span class="pill">启用</span>'
    return '<span class="pill off">关闭</span>'


def _txt_pill(config: dict) -> str:
    if config.get("txt_verified"):
        return '<span class="pill">已验证</span>'
    return '<span class="pill warn">待验证</span>'


def _config_rows(ns: dict, *, compact: bool = False) -> str:
    rows = []
    for config in _sorted_configs():
        status = _status_pill(config)
        actions = ""
        if not compact:
            actions = f"""
            <div class="row-actions">
              <form method="post" action="/admin/sso/configs/{_safe(config['id'])}/toggle">
                <button class="btn secondary" type="submit">{'关闭' if config.get('enabled', True) else '启用'}</button>
              </form>
              <a class="btn soft" href="{_safe(_workspace_url('edit', current=str(config['id'])))}">编辑</a>
            </div>
            """
        rows.append(
            f"""
            <article class="sso-row">
              <div>
                <h2 class="sso-name">{_safe(config.get('domain'))}</h2>
                <div class="sso-meta">{_safe(config.get('slug'))} · {_safe(config.get('base_url'))}</div>
              </div>
              <div class="row-actions">{status}{actions}</div>
            </article>
            """
        )
    if not rows:
        return '<div class="panel empty">还没有 SSO 配置，先新增一个。</div>'
    return "\n".join(rows)


def _sorted_configs() -> list[dict]:
    configs = list(STATE["configs"].values())
    return sorted(configs, key=lambda item: (not bool(item.get("enabled", True)), str(item.get("domain") or "")))


def _current_config(current_id: str = "") -> dict | None:
    configs = STATE["configs"]
    if current_id and current_id in configs:
        return configs[current_id]
    return _sorted_configs()[0] if configs else None


def _section_home(ns: dict) -> str:
    active_count = sum(1 for item in STATE["configs"].values() if item.get("enabled", True))
    verified_count = sum(1 for item in STATE["configs"].values() if item.get("txt_verified"))
    invitations = ns["invitations"]
    profiles = ns["profiles"]
    settings = ns.get("app_settings", {})
    cf_status = "已开启" if bool(settings.get("turnstile_enabled", False)) else "未开启"
    invite_status = "已开启" if bool(settings.get("invite_required", True)) else "未开启"
    return f"""
    <section class="stat-grid">
      <div class="stat"><b>{len(STATE['configs'])}</b><span>SSO 配置</span></div>
      <div class="stat"><b>{active_count}</b><span>已启用</span></div>
      <div class="stat"><b>{verified_count}</b><span>TXT 已验证</span></div>
      <div class="stat"><b>{len(invitations)}</b><span>可管理卡密</span></div>
    </section>
    <section class="panel" style="margin-bottom:14px">
      <div class="toolbar">
        <div>
          <strong>注册安全</strong>
          <p class="muted" style="margin:6px 0 0">Cloudflare 验证：{_safe(cf_status)} · 邀请码注册：{_safe(invite_status)} · 授权邮箱上限：{_safe(ns['max_authorized_emails_per_user']())}</p>
        </div>
        <a class="btn soft" href="{_safe(_workspace_url('security'))}">管理注册安全</a>
      </div>
    </section>
    <div class="grid">{_config_rows(ns)}</div>
    <section class="panel" style="margin-top:14px">
      <strong>邮箱记录</strong>
      <p class="muted">当前已注册 {len(profiles)} 个 SSO 账号，授权邮箱前缀会在用户进行 ChatGPT SSO 授权时记录在账号资料里。</p>
    </section>
    """


def _checked(value: bool) -> str:
    return "checked" if value else ""


def _section_security(ns: dict) -> str:
    settings = ns.get("app_settings", {})
    allowed_prefixes = ", ".join(ns.get("_runtime_allowed_prefixes", lambda: set())())
    return f"""
    <section class="panel">
      <form method="post" action="/admin/sso/security">
        <div class="grid two">
          <div>
            <h2 class="sso-name">注册策略</h2>
            <label class="field"><span><input type="checkbox" name="invite_required" value="on" {_checked(bool(settings.get('invite_required', True)))}> 开启邀请码注册</span></label>
            <label class="field"><span><input type="checkbox" name="allow_any_prefix" value="on" {_checked(bool(settings.get('allow_any_prefix', False)))}> 允许任意邮箱前缀注册</span></label>
            <div class="field">
              <label for="allowed_prefixes">允许的邮箱前缀</label>
              <textarea id="allowed_prefixes" name="allowed_prefixes" placeholder="alice, bob, charlie">{_safe(allowed_prefixes)}</textarea>
              <p class="muted">未开启“任意前缀”时，只允许这里列出的前缀注册。</p>
            </div>
            <div class="field">
              <label for="max_authorized_emails_per_user">每个用户可授权邮箱数量</label>
              <input id="max_authorized_emails_per_user" name="max_authorized_emails_per_user" type="number" min="1" max="100" value="{_safe(ns['max_authorized_emails_per_user']())}">
            </div>
          </div>
          <div>
            <h2 class="sso-name">Cloudflare Turnstile</h2>
            <label class="field"><span><input type="checkbox" name="turnstile_enabled" value="on" {_checked(bool(settings.get('turnstile_enabled', False)))}> 开启 CF 验证</span></label>
            <div class="field">
              <label for="turnstile_site_key">Turnstile Site Key</label>
              <input id="turnstile_site_key" name="turnstile_site_key" value="{_safe(settings.get('turnstile_site_key'))}" placeholder="0x4AAAA...">
            </div>
            <div class="field">
              <label for="turnstile_secret_key">Turnstile Secret Key</label>
              <input id="turnstile_secret_key" name="turnstile_secret_key" type="password" placeholder="留空则保持当前密钥">
            </div>
            <p class="muted">开启后会保护用户登录、注册、OIDC 授权和管理员登录。</p>
          </div>
        </div>
        <button class="btn" type="submit">保存注册安全设置</button>
      </form>
    </section>
    """


def _section_txt(ns: dict, notice: str = "") -> str:
    cards = []
    for config in _sorted_configs():
        last_checked = ns["fmt_time"](config.get("txt_last_checked_at"))
        error = str(config.get("txt_last_error") or "").strip()
        error_html = f'<p class="muted">最近结果：{_safe(error)}</p>' if error else ""
        cards.append(
            f"""
            <section class="panel">
              <div class="toolbar">
                <div>
                  <h2 class="sso-name">{_safe(config.get('domain'))}</h2>
                  <div class="sso-meta">最近检查：{_safe(last_checked)}</div>
                </div>
                {_txt_pill(config)}
              </div>
              <div class="field-row">
                <div class="field"><label>TXT 名称</label><code class="code-line">{_safe(config.get('txt_name'))}</code></div>
                <div class="field"><label>TXT 值</label><code class="code-line">{_safe(config.get('txt_value'))}</code></div>
              </div>
              {error_html}
              <div class="row-actions">
                <form method="post" action="/admin/sso/configs/{_safe(config['id'])}/txt/check"><button class="btn" type="submit">检查 TXT</button></form>
                <form method="post" action="/admin/sso/configs/{_safe(config['id'])}/txt/regenerate"><button class="btn secondary" type="submit">重新生成</button></form>
                <form method="post" action="/admin/sso/configs/{_safe(config['id'])}/txt/mark"><button class="btn soft" type="submit">{'取消通过' if config.get('txt_verified') else '标记通过'}</button></form>
              </div>
            </section>
            """
        )
    return '<div class="grid">' + "\n".join(cards or ['<div class="panel empty">暂无可验证的 SSO 配置。</div>']) + "</div>"


def _section_list(ns: dict) -> str:
    rows = []
    for config in _sorted_configs():
        rows.append(
            f"""
            <tr>
              <td class="check-cell"><input type="checkbox" name="selected_configs" value="{_safe(config['id'])}" form="bulkConfigForm"></td>
              <td><strong>{_safe(config.get('domain'))}</strong><br><span class="muted">{_safe(config.get('slug'))}</span></td>
              <td>{_safe(config.get('base_url'))}<br><span class="muted">{_safe(config.get('application_login_url') or '-')}</span></td>
              <td>{_status_pill(config)}</td>
              <td>{_txt_pill(config)}</td>
              <td class="row-actions">
                <form method="post" action="/admin/sso/configs/{_safe(config['id'])}/toggle"><button class="btn secondary" type="submit">{'关闭' if config.get('enabled', True) else '启用'}</button></form>
                <a class="btn soft" href="{_safe(_workspace_url('edit', current=str(config['id'])))}">编辑</a>
              </td>
            </tr>
            """
        )
    if not rows:
        rows.append('<tr><td colspan="6" class="empty">还没有 SSO 配置。</td></tr>')
    return f"""
    <form id="bulkConfigForm" method="post" action="/admin/sso/configs/bulk-delete" onsubmit="return confirm('确定删除选中的 SSO 配置？');"></form>
    <div class="toolbar">
      <a class="btn" href="{_safe(_workspace_url('add'))}">新增 SSO</a>
      <button class="btn danger" form="bulkConfigForm" type="submit">删除选中</button>
    </div>
    <section class="panel table-wrap">
      <table>
        <thead><tr><th><input type="checkbox" data-check-all="input[name='selected_configs']"></th><th>域名</th><th>Provider URL</th><th>状态</th><th>TXT</th><th>操作</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>
    """


def _section_edit(ns: dict, current_id: str = "") -> str:
    config = _current_config(current_id)
    if not config:
        return '<section class="panel empty">暂无 SSO 配置可编辑。</section>'
    options = "".join(
        f'<option value="{_safe(item["id"])}" {"selected" if item["id"] == config["id"] else ""}>{_safe(item.get("domain"))}</option>'
        for item in _sorted_configs()
    )
    checked = "checked" if config.get("enabled", True) else ""
    verified = "checked" if config.get("txt_verified") else ""
    return f"""
    <section class="panel">
      <form method="get" action="/admin/sso" class="field-row">
        <input type="hidden" name="section" value="edit">
        <div class="field"><label for="current">当前 SSO</label><select id="current" name="current">{options}</select></div>
        <div class="field"><label>&nbsp;</label><button class="btn secondary" type="submit">切换编辑</button></div>
      </form>
    </section>
    <section class="panel" style="margin-top:14px">
      <form method="post" action="/admin/sso/configs/{_safe(config['id'])}/update">
        <div class="field-row">
          <div class="field"><label for="domain">域名</label><input id="domain" name="domain" value="{_safe(config.get('domain'))}" required></div>
          <div class="field"><label for="slug">Slug</label><input id="slug" name="slug" value="{_safe(config.get('slug'))}" required></div>
        </div>
        <div class="field-row">
          <div class="field"><label for="base_url">Provider URL</label><input id="base_url" name="base_url" value="{_safe(config.get('base_url'))}" required></div>
          <div class="field"><label for="issuer">Issuer</label><input id="issuer" name="issuer" value="{_safe(config.get('issuer'))}"></div>
        </div>
        <div class="field-row">
          <div class="field"><label for="client_id">Client ID</label><input id="client_id" name="client_id" value="{_safe(config.get('client_id'))}"></div>
          <div class="field"><label for="client_secret">Client Secret</label><input id="client_secret" name="client_secret" value="{_safe(config.get('client_secret'))}"></div>
        </div>
        <div class="field"><label for="redirect_uri">Redirect URI</label><input id="redirect_uri" name="redirect_uri" value="{_safe(config.get('redirect_uri'))}"></div>
        <div class="field"><label for="application_login_url">Application Login URL</label><input id="application_login_url" name="application_login_url" value="{_safe(config.get('application_login_url'))}" placeholder="https://chatgpt.com/auth/login?sso=true&connection=..."></div>
        <div class="field"><label for="notes">备注</label><textarea id="notes" name="notes">{_safe(config.get('notes'))}</textarea></div>
        <div class="field-row">
          <label><input type="checkbox" name="enabled" value="on" {checked}> 启用这个 SSO</label>
          <label><input type="checkbox" name="txt_verified" value="on" {verified}> TXT 已验证</label>
        </div>
        <button class="btn" type="submit">保存当前 SSO</button>
      </form>
    </section>
    """


def _section_batch(ns: dict) -> str:
    settings = STATE["settings"]
    return f"""
    <section class="panel">
      <form method="post" action="/admin/sso/batch-settings">
        <div class="field-row">
          <div class="field"><label for="public_provider_url">公共 Provider</label><input id="public_provider_url" name="public_provider_url" value="{_safe(settings.get('public_provider_url'))}" required></div>
          <div class="field"><label for="issuer_base">Issuer 基础地址</label><input id="issuer_base" name="issuer_base" value="{_safe(settings.get('issuer_base'))}"></div>
        </div>
        <div class="field"><label for="redirect_template">Redirect URI 模板</label><input id="redirect_template" name="redirect_template" value="{_safe(settings.get('redirect_template'))}" placeholder="https://example.com/{{slug}}/callback"></div>
        <div class="field-row">
          <div class="field"><label for="provider_mode">Provider 模式</label><select id="provider_mode" name="provider_mode"><option value="公共 Provider" {'selected' if settings.get('provider_mode') == '公共 Provider' else ''}>公共 Provider</option><option value="独立 Provider" {'selected' if settings.get('provider_mode') == '独立 Provider' else ''}>独立 Provider</option></select></div>
          <div class="field"><label for="apply_to">应用范围</label><select id="apply_to" name="apply_to"><option value="all">全部配置</option><option value="enabled">仅启用配置</option></select></div>
        </div>
        <p class="muted">保存后会按 Slug 批量更新 Provider URL、Issuer 和 Redirect URI，域名与 TXT 记录不会被覆盖。</p>
        <button class="btn" type="submit">应用批量基础设置</button>
      </form>
    </section>
    """


def _section_add(ns: dict) -> str:
    provider = _safe(STATE["settings"].get("public_provider_url") or _default_provider_url(ns))
    return f"""
    <section class="panel">
      <form method="post" action="/admin/sso/configs/add">
        <div class="field-row">
          <div class="field"><label for="domain">域名</label><input id="domain" name="domain" placeholder="edu.example.com" required></div>
          <div class="field"><label for="slug">Slug</label><input id="slug" name="slug" placeholder="edu-example"></div>
        </div>
        <div class="field-row">
          <div class="field"><label for="base_url">Provider URL</label><input id="base_url" name="base_url" placeholder="{provider}/edu-example"></div>
          <div class="field"><label for="issuer">Issuer</label><input id="issuer" name="issuer" placeholder="{provider}"></div>
        </div>
        <div class="field-row">
          <div class="field"><label for="client_id">Client ID</label><input id="client_id" name="client_id" placeholder="client-id"></div>
          <div class="field"><label for="client_secret">Client Secret</label><input id="client_secret" name="client_secret" placeholder="可留空后续补充"></div>
        </div>
        <div class="field"><label for="redirect_uri">Redirect URI</label><input id="redirect_uri" name="redirect_uri" placeholder="{provider}/edu-example/callback"></div>
        <div class="field"><label for="application_login_url">Application Login URL</label><input id="application_login_url" name="application_login_url" placeholder="https://chatgpt.com/auth/login?sso=true&connection=..."></div>
        <div class="field"><label for="notes">备注</label><textarea id="notes" name="notes"></textarea></div>
        <label class="field"><span><input type="checkbox" name="enabled" value="on" checked> 创建后立即启用</span></label>
        <button class="btn" type="submit">新增 SSO</button>
      </form>
    </section>
    """


def _section_cards(ns: dict) -> str:
    return """
    <section class="panel">
      <form method="post" action="/admin/sso/cards/generate">
        <div class="field-row">
          <div class="field"><label for="count">生成数量</label><input id="count" name="count" type="number" min="1" max="100" value="1"></div>
          <div class="field"><label for="max_uses">每张可用次数</label><input id="max_uses" name="max_uses" type="number" min="1" max="999" value="1"></div>
        </div>
        <div class="field-row">
          <div class="field"><label for="expires_days">有效天数</label><input id="expires_days" name="expires_days" type="number" min="0" max="365" value="7"></div>
          <div class="field"><label for="note">备注</label><input id="note" name="note" placeholder="例如：6 月批量发放"></div>
        </div>
        <p class="muted">这里生成的是现有注册邀请码，可直接用于用户注册。</p>
        <button class="btn" type="submit">生成卡密</button>
      </form>
    </section>
    """


def _section_latest_cards(ns: dict) -> str:
    invitations = ns["invitations"]
    rows = []
    for invite in sorted(invitations.values(), key=lambda item: int(item.get("created_at") or 0), reverse=True):
        code = str(invite.get("code") or "")
        rows.append(
            f"""
            <tr>
              <td class="check-cell"><input type="checkbox" name="selected_cards" value="{_safe(code)}" form="bulkCardsForm"></td>
              <td><code>{_safe(code)}</code><br><span class="muted">{_safe(invite.get('note') or '-')}</span></td>
              <td>{int(invite.get('uses') or 0)} / {int(invite.get('max_uses') or 1)}</td>
              <td>{ns['fmt_time'](invite.get('expires_at'))}</td>
              <td>{'<span class="pill">启用</span>' if invite.get('active', True) else '<span class="pill off">关闭</span>'}</td>
              <td class="row-actions">
                <form method="post" action="/admin/sso/cards/{_safe(code)}/toggle"><button class="btn secondary" type="submit">{'关闭' if invite.get('active', True) else '启用'}</button></form>
                <form method="post" action="/admin/sso/cards/{_safe(code)}/delete" onsubmit="return confirm('确定删除这张卡密？');"><button class="btn danger" type="submit">删除</button></form>
              </td>
            </tr>
            """
        )
    if not rows:
        rows.append('<tr><td colspan="6" class="empty">还没有卡密，先生成一批。</td></tr>')
    return f"""
    <form id="bulkCardsForm" method="post" action="/admin/sso/cards/bulk-delete" onsubmit="return confirm('确定删除选中的卡密？');"></form>
    <div class="toolbar">
      <a class="btn" href="{_safe(_workspace_url('cards'))}">生成卡密</a>
      <button class="btn danger" form="bulkCardsForm" type="submit">删除选中</button>
    </div>
    <section class="panel table-wrap">
      <table>
        <thead><tr><th><input type="checkbox" data-check-all="input[name='selected_cards']"></th><th>卡密</th><th>使用</th><th>过期</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>
    """


def _section_users(ns: dict) -> str:
    profiles = ns["profiles"]
    aliases_fn = ns.get("_authorized_aliases", lambda profile: [])
    rows = []
    for email, profile in sorted(
        profiles.items(), key=lambda item: int(item[1].get("registered_at") or 0), reverse=True
    ):
        aliases = [alias for alias in aliases_fn(profile) if alias.get("email") != email]
        alias_parts = []
        for alias in aliases:
            alias_email = str(alias.get("email") or "")
            alias_parts.append(
                f"""
                <div class="code-line" style="margin-top:6px">
                  <strong>{_safe(alias_email)}</strong>
                  <form method="post" action="/admin/sso/users/authorized-email/delete" onsubmit="return confirm('确定删除这个授权邮箱？');" style="margin-top:6px">
                    <input type="hidden" name="user_email" value="{_safe(email)}">
                    <input type="hidden" name="authorized_email" value="{_safe(alias_email)}">
                    <button class="btn danger" type="submit">删除授权邮箱</button>
                  </form>
                </div>
                """
            )
        rows.append(
            f"""
            <tr>
              <td class="check-cell"><input type="checkbox" name="selected_users" value="{_safe(email)}" form="bulkUserForm"></td>
              <td><strong>{_safe(email)}</strong><br><span class="muted">{_safe(profile.get('name') or email)}</span></td>
              <td><code>{_safe(profile.get('prefix') or (email.split('@', 1)[0] if '@' in email else email))}</code></td>
              <td>{_safe(email.split('@', 1)[1] if '@' in email else '-')}</td>
              <td>{1 + len(aliases)} / {ns['max_authorized_emails_per_user']()}{''.join(alias_parts)}</td>
              <td>{ns['fmt_time'](profile.get('registered_at'))}</td>
              <td>{ns['fmt_time'](profile.get('last_login_at'))}</td>
              <td class="row-actions">
                <form method="post" action="/admin/sso/users/delete" onsubmit="return confirm('确定删除这个用户？');">
                  <input type="hidden" name="email" value="{_safe(email)}">
                  <button class="btn danger" type="submit">删除</button>
                </form>
              </td>
            </tr>
            """
        )
    if not rows:
        rows.append('<tr><td colspan="8" class="empty">暂无注册用户。</td></tr>')
    return f"""
    <form id="bulkUserForm" method="post" action="/admin/sso/users/bulk-delete" onsubmit="return confirm('确定删除选中的用户？');"></form>
    <div class="toolbar">
      <span class="muted">删除用户会同时移除该账号下的授权邮箱。</span>
      <button class="btn danger" form="bulkUserForm" type="submit">删除选中用户</button>
    </div>
    <section class="panel table-wrap">
      <table>
        <thead><tr><th><input type="checkbox" data-check-all="input[name='selected_users']"></th><th>账号邮箱</th><th>前缀</th><th>域名</th><th>授权邮箱</th><th>注册时间</th><th>最近登录</th><th>操作</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>
    """


def _email_records(ns: dict) -> list[dict]:
    records = []
    profiles = ns["profiles"]
    aliases_fn = ns.get("_authorized_aliases")
    for primary_email, profile in sorted(profiles.items()):
        primary = {
            "email": primary_email,
            "account": primary_email,
            "prefix": profile.get("prefix") or primary_email.split("@", 1)[0],
            "domain": primary_email.split("@", 1)[1] if "@" in primary_email else "",
            "source": "主账号",
            "created_at": profile.get("registered_at") or 0,
            "last_used_at": profile.get("last_login_at") or 0,
        }
        records.append(primary)
        if aliases_fn:
            for alias in aliases_fn(profile):
                alias_email = alias.get("email")
                if alias_email and alias_email != primary_email:
                    records.append(
                        {
                            "email": alias_email,
                            "account": primary_email,
                            "prefix": alias.get("prefix") or alias_email.split("@", 1)[0],
                            "domain": alias.get("domain") or (alias_email.split("@", 1)[1] if "@" in alias_email else ""),
                            "source": "授权邮箱",
                            "created_at": alias.get("created_at") or 0,
                            "last_used_at": alias.get("last_used_at") or 0,
                        }
                    )
    for item in STATE["email_records"]:
        if isinstance(item, dict):
            records.append(item)
    return sorted(records, key=lambda item: int(item.get("last_used_at") or item.get("created_at") or 0), reverse=True)


def _section_emails(ns: dict) -> str:
    rows = []
    for record in _email_records(ns):
        manual = str(record.get("source") or "") == "手动记录"
        checkbox = (
            f'<input type="checkbox" name="selected_records" value="{_safe(record.get("id"))}" form="bulkEmailForm">'
            if manual
            else ""
        )
        rows.append(
            f"""
            <tr>
              <td class="check-cell">{checkbox}</td>
              <td><strong>{_safe(record.get('email'))}</strong><br><span class="muted">{_safe(record.get('source'))}</span></td>
              <td>{_safe(record.get('account'))}</td>
              <td><code>{_safe(record.get('prefix'))}</code></td>
              <td>{_safe(record.get('domain'))}</td>
              <td>{ns['fmt_time'](record.get('last_used_at') or record.get('created_at'))}</td>
            </tr>
            """
        )
    if not rows:
        rows.append('<tr><td colspan="6" class="empty">暂无邮箱记录。</td></tr>')
    return f"""
    <form id="bulkEmailForm" method="post" action="/admin/sso/email-records/bulk-delete" onsubmit="return confirm('确定删除选中的手动邮箱记录？');"></form>
    <section class="panel" style="margin-bottom:14px">
      <form method="post" action="/admin/sso/email-records/add">
        <div class="field-row">
          <div class="field"><label for="email">邮箱</label><input id="email" name="email" placeholder="alice@example.com" required></div>
          <div class="field"><label for="account">关联账号</label><input id="account" name="account" placeholder="alice@example.com"></div>
        </div>
        <button class="btn" type="submit">添加手动记录</button>
      </form>
    </section>
    <div class="toolbar"><span class="muted">主账号和授权邮箱会自动汇总；只有手动记录可批量删除。</span><button class="btn danger" form="bulkEmailForm" type="submit">删除选中</button></div>
    <section class="panel table-wrap">
      <table>
        <thead><tr><th><input type="checkbox" data-check-all="input[name='selected_records']"></th><th>邮箱</th><th>账号</th><th>前缀</th><th>域名</th><th>最近使用</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>
    """


def _section_content(ns: dict, section: str, current_id: str, notice: str) -> str:
    if section == "security":
        return _section_security(ns)
    if section == "txt":
        return _section_txt(ns, notice)
    if section == "list":
        return _section_list(ns)
    if section == "edit":
        return _section_edit(ns, current_id)
    if section == "batch":
        return _section_batch(ns)
    if section == "add":
        return _section_add(ns)
    if section == "cards":
        return _section_cards(ns)
    if section == "latest_card":
        return _section_latest_cards(ns)
    if section == "users":
        return _section_users(ns)
    if section == "emails":
        return _section_emails(ns)
    return _section_home(ns)


def render_admin(
    ns: dict,
    section: str = "home",
    current_id: str = "",
    notice: str = "",
    workspace_id: str = "",
) -> str:
    load_state(ns)
    selected_workspace = _select_workspace(ns, workspace_id)
    ACTIVE_WORKSPACE_ID.set(selected_workspace)
    section = section if section in SECTIONS else "home"
    service = _safe(ns.get("SERVICE_NAME") or "SSO")
    provider = _safe(STATE["settings"].get("public_provider_url") or _default_provider_url(ns))
    mode = _safe(STATE["settings"].get("provider_mode") or "公共 Provider")
    notice_html = f'<div class="notice">{_safe(notice)}</div>' if notice else ""
    nav = []
    for key, label in SECTIONS.items():
        active = " active" if key == section else ""
        nav.append(f'<a class="side-item{active}" href="{_safe(_workspace_url(key))}">{_safe(label)}</a>')
    content = _section_content(ns, section, current_id, notice)
    selector = _workspace_selector(ns, section, current_id)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SSO 后台 | {service}</title>
  <style>{ADMIN_CSS}</style>
</head>
<body>
  <main class="admin-shell">
    <aside class="sidebar">
      <div class="brand-row">
        <div class="brand-mark">SSO</div>
        <div><h1 class="brand-title">SSO 后台</h1><p class="brand-sub">{len(STATE['configs'])} 个配置</p></div>
      </div>
      <nav class="side-nav" aria-label="SSO 后台导航">{''.join(nav)}</nav>
      <form method="post" action="/admin/logout"><button class="logout-button" type="submit">退出</button></form>
    </aside>
    <section class="content">
      <h1 class="page-title">SSO 后台</h1>
      <p class="lead">{provider} · {mode}，域名配置按 SSO 租户独立保存。</p>
      {notice_html}
      {selector}
      {content}
    </section>
  </main>
  <script>{ADMIN_JS}</script>
</body>
</html>"""


def _update_config_from_form(config: dict, ns: dict, form: dict) -> dict:
    now = _now(ns)
    domain = str(form.get("domain") or config.get("domain") or "").strip().lower()
    slug = _slugify(str(form.get("slug") or config.get("slug") or domain))
    config.update(
        {
            "domain": domain,
            "slug": slug,
            "base_url": str(form.get("base_url") or "").strip().rstrip("/") or config.get("base_url"),
            "issuer": str(form.get("issuer") or "").strip().rstrip("/") or config.get("issuer"),
            "client_id": str(form.get("client_id") or "").strip(),
            "client_secret": str(form.get("client_secret") or "").strip(),
            "redirect_uri": str(form.get("redirect_uri") or "").strip(),
            "application_login_url": str(form.get("application_login_url") or "").strip(),
            "notes": str(form.get("notes") or "").strip(),
            "enabled": form.get("enabled") == "on",
            "txt_verified": form.get("txt_verified") == "on",
            "updated_at": now,
        }
    )
    if domain:
        config["txt_name"] = str(config.get("txt_name") or f"_sso.{domain}")
    return config


def _check_dns_txt(config: dict) -> tuple[bool, str]:
    try:
        import dns.resolver
    except Exception:
        return False, "缺少 dnspython 依赖，部署安装后可自动检查；当前可先手动标记。"

    expected = str(config.get("txt_value") or "").strip()
    host = str(config.get("txt_name") or "").strip()
    if not expected or not host:
        return False, "TXT 名称或 TXT 值为空。"
    try:
        answers = dns.resolver.resolve(host, "TXT", lifetime=6)
        values = []
        for answer in answers:
            values.append("".join(part.decode("utf-8", "ignore") for part in answer.strings))
    except Exception as exc:
        return False, f"DNS 暂未查询到匹配记录：{exc}"
    if expected in values:
        return True, "TXT 验证通过。"
    return False, "已查询到 TXT，但没有匹配当前验证值。"


def _resolve_workspace_client(ns: dict, client_id: str, redirect_uri: str = "", client_secret: str | None = None):
    load_state(ns)
    for config in STATE["configs"].values():
        if not config.get("enabled", True):
            continue
        config_client_id = str(config.get("client_id") or "").strip()
        if not config_client_id or config_client_id != client_id:
            continue
        config_redirect = str(config.get("redirect_uri") or "").strip()
        if redirect_uri and config_redirect and redirect_uri != config_redirect:
            return None
        if client_secret is not None:
            expected_secret = str(config.get("client_secret") or "")
            if expected_secret and not secrets.compare_digest(client_secret, expected_secret):
                return None
            if not expected_secret and client_secret:
                return None
        workspace_id = str(config.get("id") or config.get("slug") or "default")
        ACTIVE_WORKSPACE_ID.set(workspace_id)
        return {
            "workspace_id": workspace_id,
            "client_id": config_client_id,
            "client_secret": str(config.get("client_secret") or ""),
            "redirect_uri": config_redirect,
            "issuer": str(config.get("issuer") or ns.get("ISSUER") or ""),
        }
    legacy_resolver = ns.get("_legacy_resolve_oidc_client")
    if legacy_resolver:
        legacy = legacy_resolver(client_id, redirect_uri, client_secret)
        if legacy:
            ACTIVE_WORKSPACE_ID.set(str(legacy.get("workspace_id") or _default_workspace_id(ns)))
        return legacy
    return None


def install_workspace_runtime(ns: dict) -> None:
    global RUNTIME_INSTALLED
    if RUNTIME_INSTALLED:
        return
    load_state(ns)

    legacy_profiles = dict(ns["profiles"])
    legacy_invitations = dict(ns["invitations"])
    legacy_settings = dict(ns["app_settings"])

    settings_defaults = dict(legacy_settings)
    settings_defaults.setdefault("invite_required", True)
    settings_defaults.setdefault("allow_any_prefix", False)
    settings_defaults.setdefault("allowed_prefixes", [])
    settings_defaults.setdefault("turnstile_enabled", False)
    settings_defaults.setdefault("turnstile_site_key", "")
    settings_defaults.setdefault("turnstile_secret_key", "")
    settings_defaults.setdefault("max_authorized_emails_per_user", 3)

    profile_store = WorkspaceMapping(ns, "profiles", legacy_profiles, dict)
    invite_store = WorkspaceMapping(ns, "invitations", legacy_invitations, dict)
    settings_store = WorkspaceMapping(ns, "settings", legacy_settings, lambda: dict(settings_defaults))

    ns["profiles"] = profile_store
    ns["invitations"] = invite_store
    ns["app_settings"] = settings_store
    ns["_legacy_resolve_oidc_client"] = ns.get("resolve_oidc_client")

    def save_profiles():
        profile_store.save_current()

    def save_invitations():
        invite_store.save_current()

    def save_settings():
        settings_store.save_current()

    def active_workspace():
        return current_workspace_id(ns)

    def activate_workspace(workspace_id: str):
        ACTIVE_WORKSPACE_ID.set(_select_workspace(ns, workspace_id))

    def active_domains():
        return _workspace_domains(ns)

    ns["save_profiles"] = save_profiles
    ns["save_invitations"] = save_invitations
    ns["save_settings"] = save_settings
    ns["active_workspace_id"] = active_workspace
    ns["activate_workspace"] = activate_workspace
    ns["active_email_domains"] = active_domains
    ns["resolve_oidc_client"] = lambda client_id, redirect_uri="", client_secret=None: _resolve_workspace_client(
        ns, client_id, redirect_uri, client_secret
    )

    app = ns["app"]

    @app.middleware("http")
    async def sso_workspace_middleware(request: Request, call_next):
        requested = request.query_params.get("workspace") or request.cookies.get("admin_workspace", "")
        if requested:
            ACTIVE_WORKSPACE_ID.set(_select_workspace(ns, requested))
        response = await call_next(request)
        if request.query_params.get("workspace"):
            response.set_cookie(
                "admin_workspace",
                current_workspace_id(ns),
                max_age=30 * 86400,
                httponly=True,
                samesite="lax",
                secure=str(ns.get("ISSUER") or "").startswith("https://"),
            )
        return response

    RUNTIME_INSTALLED = True


def install(ns: dict) -> None:
    global INSTALLED
    load_state(ns)
    install_workspace_runtime(ns)
    ns["render_admin_console"] = lambda: render_admin(ns, "home")
    if INSTALLED:
        return
    app = ns["app"]

    @app.get("/admin/sso", response_class=HTMLResponse)
    def sso_admin_page(
        request: Request,
        section: str = "home",
        current: str = "",
        notice: str = "",
        workspace: str = "",
    ):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        return HTMLResponse(render_admin(ns, section, current, notice, workspace))

    @app.post("/admin/sso/configs/add", response_class=HTMLResponse)
    def sso_add_config(
        request: Request,
        domain: str = Form(...),
        slug: str = Form(""),
        base_url: str = Form(""),
        issuer: str = Form(""),
        client_id: str = Form(""),
        client_secret: str = Form(""),
        redirect_uri: str = Form(""),
        application_login_url: str = Form(""),
        notes: str = Form(""),
        enabled: str = Form(""),
    ):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        load_state(ns)
        clean_slug = _slugify(slug or domain)
        unique_slug = clean_slug
        index = 2
        while unique_slug in STATE["configs"]:
            unique_slug = f"{clean_slug}-{index}"
            index += 1
        provider = str(STATE["settings"].get("public_provider_url") or _default_provider_url(ns)).rstrip("/")
        config = _new_config(ns, domain, unique_slug, unique_slug, enabled == "on")
        config.update(
            {
                "id": unique_slug,
                "slug": unique_slug,
                "base_url": base_url.strip().rstrip("/") or f"{provider}/{unique_slug}",
                "issuer": issuer.strip().rstrip("/") or str(STATE["settings"].get("issuer_base") or provider).rstrip("/"),
                "client_id": client_id.strip() or unique_slug,
                "client_secret": client_secret.strip(),
                "redirect_uri": redirect_uri.strip()
                or str(STATE["settings"].get("redirect_template") or f"{provider}/{{slug}}/callback").replace(
                    "{slug}", unique_slug
                ),
                "application_login_url": application_login_url.strip(),
                "notes": notes.strip(),
            }
        )
        STATE["configs"][unique_slug] = config
        save_configs(ns)
        return _redirect("edit", current=unique_slug, notice="SSO 配置已新增。")

    @app.post("/admin/sso/configs/{config_id}/toggle", response_class=HTMLResponse)
    def sso_toggle_config(request: Request, config_id: str):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        load_state(ns)
        if config_id in STATE["configs"]:
            STATE["configs"][config_id]["enabled"] = not STATE["configs"][config_id].get("enabled", True)
            STATE["configs"][config_id]["updated_at"] = _now(ns)
            save_configs(ns)
        return _redirect("home")

    @app.post("/admin/sso/configs/{config_id}/update", response_class=HTMLResponse)
    def sso_update_config(
        request: Request,
        config_id: str,
        domain: str = Form(...),
        slug: str = Form(...),
        base_url: str = Form(""),
        issuer: str = Form(""),
        client_id: str = Form(""),
        client_secret: str = Form(""),
        redirect_uri: str = Form(""),
        application_login_url: str = Form(""),
        notes: str = Form(""),
        enabled: str = Form(""),
        txt_verified: str = Form(""),
    ):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        load_state(ns)
        config = STATE["configs"].get(config_id)
        if not config:
            return _redirect("list", notice="未找到要编辑的 SSO。")
        updated = _update_config_from_form(
            config,
            ns,
            {
                "domain": domain,
                "slug": slug,
                "base_url": base_url,
                "issuer": issuer,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "application_login_url": application_login_url,
                "notes": notes,
                "enabled": enabled,
                "txt_verified": txt_verified,
            },
        )
        new_id = updated["slug"]
        updated["id"] = new_id
        if new_id != config_id:
            STATE["configs"].pop(config_id, None)
        STATE["configs"][new_id] = updated
        save_configs(ns)
        return _redirect("edit", current=new_id, notice="当前 SSO 已保存。")

    @app.post("/admin/sso/configs/bulk-delete", response_class=HTMLResponse)
    def sso_bulk_delete_configs(request: Request, selected_configs: list[str] = Form(default=[])):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        load_state(ns)
        changed = False
        for config_id in selected_configs:
            if config_id in STATE["configs"]:
                STATE["configs"].pop(config_id)
                changed = True
        if changed:
            save_configs(ns)
        return _redirect("list", notice="已删除选中的 SSO 配置。" if changed else "")

    @app.post("/admin/sso/configs/{config_id}/txt/regenerate", response_class=HTMLResponse)
    def sso_regenerate_txt(request: Request, config_id: str):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        load_state(ns)
        config = STATE["configs"].get(config_id)
        if config:
            config["txt_value"] = _make_txt_token()
            config["txt_verified"] = False
            config["txt_last_error"] = "已重新生成验证值，请更新 DNS TXT 记录。"
            config["updated_at"] = _now(ns)
            save_configs(ns)
        return _redirect("txt")

    @app.post("/admin/sso/configs/{config_id}/txt/mark", response_class=HTMLResponse)
    def sso_mark_txt(request: Request, config_id: str):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        load_state(ns)
        config = STATE["configs"].get(config_id)
        if config:
            config["txt_verified"] = not config.get("txt_verified", False)
            config["txt_last_checked_at"] = _now(ns)
            config["txt_last_error"] = "管理员手动标记。"
            save_configs(ns)
        return _redirect("txt")

    @app.post("/admin/sso/configs/{config_id}/txt/check", response_class=HTMLResponse)
    def sso_check_txt(request: Request, config_id: str):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        load_state(ns)
        config = STATE["configs"].get(config_id)
        notice = ""
        if config:
            ok, message = _check_dns_txt(config)
            config["txt_verified"] = ok
            config["txt_last_checked_at"] = _now(ns)
            config["txt_last_error"] = message
            save_configs(ns)
            notice = message
        return _redirect("txt", notice=notice)

    @app.post("/admin/sso/batch-settings", response_class=HTMLResponse)
    def sso_batch_settings(
        request: Request,
        public_provider_url: str = Form(...),
        issuer_base: str = Form(""),
        redirect_template: str = Form(""),
        provider_mode: str = Form("公共 Provider"),
        apply_to: str = Form("all"),
    ):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        load_state(ns)
        provider = public_provider_url.strip().rstrip("/")
        issuer = issuer_base.strip().rstrip("/") or provider
        template = redirect_template.strip() or f"{provider}/{{slug}}/callback"
        STATE["settings"].update(
            {
                "public_provider_url": provider,
                "issuer_base": issuer,
                "redirect_template": template,
                "provider_mode": provider_mode.strip() or "公共 Provider",
            }
        )
        for config in STATE["configs"].values():
            if apply_to == "enabled" and not config.get("enabled", True):
                continue
            slug = str(config.get("slug") or config.get("id"))
            config["provider_url"] = provider
            config["base_url"] = f"{provider}/{slug}"
            config["issuer"] = issuer
            config["redirect_uri"] = template.replace("{slug}", slug).replace("{domain}", str(config.get("domain") or ""))
            config["updated_at"] = _now(ns)
        save_settings(ns)
        save_configs(ns)
        return _redirect("batch", notice="批量基础设置已应用。")

    @app.post("/admin/sso/security", response_class=HTMLResponse)
    def sso_update_security(
        request: Request,
        invite_required: str = Form(""),
        allow_any_prefix: str = Form(""),
        allowed_prefixes: str = Form(""),
        turnstile_enabled: str = Form(""),
        turnstile_site_key: str = Form(""),
        turnstile_secret_key: str = Form(""),
        max_authorized_emails_per_user: int = Form(3),
    ):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        settings = ns["app_settings"]
        settings["invite_required"] = invite_required == "on"
        settings["allow_any_prefix"] = allow_any_prefix == "on"
        settings["allowed_prefixes"] = ns["normalize_prefixes"](allowed_prefixes)
        settings["turnstile_enabled"] = turnstile_enabled == "on"
        settings["turnstile_site_key"] = turnstile_site_key.strip()
        if turnstile_secret_key.strip():
            settings["turnstile_secret_key"] = turnstile_secret_key.strip()
        try:
            settings["max_authorized_emails_per_user"] = max(
                1, min(int(max_authorized_emails_per_user or 3), 100)
            )
        except (TypeError, ValueError):
            settings["max_authorized_emails_per_user"] = 3
        ns["save_settings"]()
        return _redirect("security", notice="注册安全设置已保存。")

    @app.post("/admin/sso/cards/generate", response_class=HTMLResponse)
    def sso_generate_cards(
        request: Request,
        count: int = Form(1),
        note: str = Form(""),
        max_uses: int = Form(1),
        expires_days: int = Form(7),
    ):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        invitations = ns["invitations"]
        count = max(1, min(int(count or 1), 100))
        max_uses = max(1, min(int(max_uses or 1), 999))
        expires_days = max(0, min(int(expires_days or 0), 365))
        now = _now(ns)
        for _ in range(count):
            code = ns["make_invite_code"]()
            while code in invitations:
                code = ns["make_invite_code"]()
            invitations[code] = {
                "code": code,
                "note": note.strip(),
                "max_uses": max_uses,
                "uses": 0,
                "active": True,
                "created_at": now,
                "expires_at": now + expires_days * 86400 if expires_days else 0,
                "used_by": [],
            }
        ns["save_invitations"]()
        return _redirect("latest_card", notice=f"已生成 {count} 张卡密。")

    @app.post("/admin/sso/cards/{code}/toggle", response_class=HTMLResponse)
    def sso_toggle_card(request: Request, code: str):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        key = ns["clean_invite_code"](code)
        invitations = ns["invitations"]
        if key in invitations:
            invitations[key]["active"] = not invitations[key].get("active", True)
            ns["save_invitations"]()
        return _redirect("latest_card")

    @app.post("/admin/sso/cards/{code}/delete", response_class=HTMLResponse)
    def sso_delete_card(request: Request, code: str):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        key = ns["clean_invite_code"](code)
        invitations = ns["invitations"]
        if key in invitations:
            invitations.pop(key)
            ns["save_invitations"]()
        return _redirect("latest_card")

    @app.post("/admin/sso/cards/bulk-delete", response_class=HTMLResponse)
    def sso_bulk_delete_cards(request: Request, selected_cards: list[str] = Form(default=[])):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        invitations = ns["invitations"]
        changed = False
        for code in selected_cards:
            key = ns["clean_invite_code"](code)
            if key in invitations:
                invitations.pop(key)
                changed = True
        if changed:
            ns["save_invitations"]()
        return _redirect("latest_card", notice="已删除选中的卡密。" if changed else "")

    @app.post("/admin/sso/users/delete", response_class=HTMLResponse)
    def sso_delete_user(request: Request, email: str = Form(...)):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        profiles = ns["profiles"]
        key = email.strip().lower()
        if key in profiles:
            profiles.pop(key)
            ns["save_profiles"]()
        return _redirect("users", notice="用户已删除。")

    @app.post("/admin/sso/users/bulk-delete", response_class=HTMLResponse)
    def sso_bulk_delete_users(request: Request, selected_users: list[str] = Form(default=[])):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        profiles = ns["profiles"]
        changed = False
        for email in selected_users:
            key = email.strip().lower()
            if key in profiles:
                profiles.pop(key)
                changed = True
        if changed:
            ns["save_profiles"]()
        return _redirect("users", notice="已删除选中的用户。" if changed else "")

    @app.post("/admin/sso/users/authorized-email/delete", response_class=HTMLResponse)
    def sso_delete_authorized_email(
        request: Request,
        user_email: str = Form(...),
        authorized_email: str = Form(...),
    ):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        profiles = ns["profiles"]
        owner = user_email.strip().lower()
        target = authorized_email.strip().lower()
        profile = profiles.get(owner)
        aliases_fn = ns.get("_authorized_aliases", lambda profile: [])
        if profile:
            profile["authorized_emails"] = [
                alias for alias in aliases_fn(profile) if alias.get("email") != target
            ]
            if profile.get("last_authorized_email") == target:
                profile["last_authorized_email"] = owner
            ns["save_profiles"]()
        return _redirect("users", notice="授权邮箱已删除。")

    @app.post("/admin/sso/email-records/add", response_class=HTMLResponse)
    def sso_add_email_record(request: Request, email: str = Form(...), account: str = Form("")):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        load_state(ns)
        normalized = email.strip().lower()
        STATE["email_records"].append(
            {
                "email": normalized,
                "id": f"mail-{secrets.token_hex(6)}",
                "account": account.strip().lower() or normalized,
                "prefix": normalized.split("@", 1)[0] if "@" in normalized else normalized,
                "domain": normalized.split("@", 1)[1] if "@" in normalized else "",
                "source": "手动记录",
                "created_at": _now(ns),
                "last_used_at": _now(ns),
            }
        )
        save_email_records(ns)
        return _redirect("emails", notice="邮箱记录已添加。")

    @app.post("/admin/sso/email-records/bulk-delete", response_class=HTMLResponse)
    def sso_bulk_delete_email_records(request: Request, selected_records: list[str] = Form(default=[])):
        if not ns["is_admin_request"](request):
            return _admin_redirect()
        load_state(ns)
        selected = {str(record_id) for record_id in selected_records}
        manual_records = [item for item in STATE["email_records"] if isinstance(item, dict)]
        kept = [item for item in manual_records if str(item.get("id") or "") not in selected]
        if len(kept) != len(manual_records):
            STATE["email_records"] = kept
            save_email_records(ns)
        return _redirect("emails", notice="已删除选中的手动邮箱记录。")

    INSTALLED = True
