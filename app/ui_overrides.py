import html


def _safe(value) -> str:
    return html.escape(str(value or ""))


def _brand(ns) -> str:
    return _safe(ns["SERVICE_NAME"])


def _bg(ns) -> str:
    return _safe(ns["LOGIN_BACKGROUND_URL"])


def _shell_css() -> str:
    return """
    :root { color-scheme: light; --ink:#111827; --soft:#5b6472; --line:rgba(148,163,184,.26); --panel:rgba(255,255,255,.84); --dark:#172033; --blue:#2563eb; --teal:#0f766e; --amber:#b45309; --rose:#be123c; --mint:#13a38d; --gold:#d99a2b; --mist:rgba(248,251,255,.72); }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; color:var(--ink); letter-spacing:0; }
    ::selection { color:#fff; background:#172033; }
    a { color:inherit; text-decoration:none; }
    .glass { border:1px solid rgba(255,255,255,.72); border-radius:8px; background:var(--panel); box-shadow:0 22px 70px rgba(31,46,71,.14); backdrop-filter:blur(18px); }
    .nav { position:sticky; top:0; z-index:5; border-bottom:1px solid rgba(148,163,184,.24); background:rgba(248,251,255,.76); backdrop-filter:blur(18px); }
    .nav-inner { width:min(1180px, calc(100% - 32px)); min-height:68px; margin:0 auto; display:flex; align-items:center; justify-content:space-between; gap:16px; }
    .brand { display:inline-flex; align-items:center; gap:10px; font-weight:900; }
    .mark { display:grid; place-items:center; width:36px; height:36px; border-radius:8px; color:#fff; background:linear-gradient(135deg, var(--dark), #2563eb 58%, #0f766e); font-size:13px; box-shadow:0 12px 30px rgba(23,32,51,.20); }
    .actions { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
    .btn, button.btn { display:inline-flex; align-items:center; justify-content:center; gap:8px; min-height:40px; padding:0 16px; border:0; border-radius:8px; color:#fff; background:var(--dark); font:inherit; font-weight:900; cursor:pointer; box-shadow:0 14px 30px rgba(23,32,51,.18); transition:transform .18s ease, box-shadow .18s ease, background .18s ease; }
    .btn:hover, button.btn:hover { transform:translateY(-1px); box-shadow:0 18px 36px rgba(23,32,51,.24); }
    .btn.secondary, button.secondary { color:var(--dark); background:rgba(255,255,255,.78); border:1px solid rgba(148,163,184,.38); box-shadow:none; }
    .btn.danger { background:#991b1b; }
    .shell { width:min(1180px, calc(100% - 32px)); margin:0 auto; }
    .muted { color:var(--soft); }
    code { padding:4px 7px; border-radius:6px; background:rgba(226,232,240,.78); overflow-wrap:anywhere; }
    input, select, textarea { width:100%; min-height:44px; padding:10px 12px; color:#1f2937; background:rgba(255,255,255,.88); border:1px solid rgba(148,163,184,.56); border-radius:8px; font:inherit; outline:none; }
    input:focus, select:focus, textarea:focus { border-color:#2563eb; box-shadow:0 0 0 4px rgba(37,99,235,.14); }
    label { display:block; margin:14px 0 7px; color:#263241; font-size:14px; font-weight:900; }
    table { width:100%; border-collapse:collapse; font-size:14px; }
    th, td { padding:12px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:middle; }
    th { color:#516071; font-size:12px; text-transform:uppercase; letter-spacing:0; }
    .pill { display:inline-flex; align-items:center; padding:4px 8px; border-radius:999px; background:rgba(37,99,235,.12); color:#1d4ed8; font-weight:900; font-size:12px; }
    .empty { color:#64748b; text-align:center; }
    @keyframes riseIn { from { opacity:0; transform:translateY(18px); } to { opacity:1; transform:translateY(0); } }
    @keyframes softGlow { 0%, 100% { opacity:.66; transform:translate3d(0,0,0) scale(1); } 50% { opacity:.92; transform:translate3d(0,-8px,0) scale(1.015); } }
    @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration:.001ms !important; animation-iteration-count:1 !important; scroll-behavior:auto !important; transition-duration:.001ms !important; } }
    @media (max-width: 880px) { .nav-inner { align-items:flex-start; flex-direction:column; padding:14px 0; } }
    """


def root_page(ns) -> str:
    service = _brand(ns)
    bg = _bg(ns)
    issuer = _safe(ns["ISSUER"] or "not configured")
    domains = _safe(", ".join(ns["EMAIL_DOMAINS"]) or "not configured")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{service}</title>
  <style>
    {_shell_css()}
    body {{ position:relative; background:linear-gradient(120deg, rgba(15,23,42,.76), rgba(37,99,235,.28) 46%, rgba(20,184,166,.20)), url("{bg}"); background-position:center; background-size:cover; background-attachment:fixed; overflow-x:hidden; }}
    body::before {{ content:""; position:fixed; inset:0; pointer-events:none; background:linear-gradient(90deg, rgba(255,255,255,.10) 1px, transparent 1px), linear-gradient(0deg, rgba(255,255,255,.08) 1px, transparent 1px); background-size:64px 64px; mask-image:linear-gradient(180deg, rgba(0,0,0,.55), transparent 72%); }}
    body::after {{ content:""; position:fixed; right:-18vw; top:12vh; width:52vw; height:52vw; pointer-events:none; background:radial-gradient(circle, rgba(19,163,141,.26), transparent 62%); animation:softGlow 8s ease-in-out infinite; }}
    .hero {{ min-height:calc(100vh - 68px); display:grid; grid-template-columns:minmax(0,1.05fr) minmax(320px,.95fr); gap:42px; align-items:center; padding:54px 0 76px; }}
    .copy {{ position:relative; color:#fff; text-shadow:0 18px 45px rgba(6,13,28,.34); animation:riseIn .62s ease both; }}
    .copy p {{ margin:0 0 14px; font-weight:900; opacity:.92; }}
    h1 {{ margin:0; max-width:720px; font-size:58px; line-height:1.08; letter-spacing:0; }}
    .lead {{ max-width:590px; margin:22px 0 30px; color:rgba(255,255,255,.9); font-size:17px; line-height:1.75; font-weight:700; }}
    .status {{ position:relative; padding:28px; animation:riseIn .62s ease .08s both; overflow:hidden; }}
    .status::before {{ content:""; position:absolute; inset:0; border-top:3px solid rgba(19,163,141,.56); pointer-events:none; }}
    .status h2 {{ margin:0 0 18px; font-size:24px; }}
    .metric {{ display:grid; grid-template-columns:110px 1fr; gap:12px; padding:14px 0; border-top:1px solid var(--line); color:var(--soft); }}
    .metric strong {{ color:var(--ink); }}
    .band {{ padding:56px 0 74px; background:rgba(248,251,255,.76); backdrop-filter:blur(12px); }}
    .cards {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:16px; }}
    .card {{ padding:22px; min-height:150px; transition:transform .18s ease, box-shadow .18s ease; }}
    .card:hover {{ transform:translateY(-3px); box-shadow:0 28px 76px rgba(31,46,71,.18); }}
    .icon {{ display:grid; place-items:center; width:42px; height:42px; border-radius:8px; color:#fff; background:var(--blue); font-weight:900; margin-bottom:14px; }}
    .card:nth-child(2) .icon {{ background:var(--teal); }}
    .card:nth-child(3) .icon {{ background:var(--amber); }}
    .card h3 {{ margin:0 0 8px; font-size:19px; }}
    .card p {{ margin:0; color:var(--soft); line-height:1.65; font-weight:650; }}
    @media (max-width: 880px) {{ .hero, .cards {{ grid-template-columns:1fr; }} h1 {{ font-size:40px; }} }}
  </style>
</head>
<body>
  <nav class="nav"><div class="nav-inner"><a class="brand" href="/"><span class="mark">SSO</span><span>{service}</span></a><div class="actions"><a class="btn secondary" href="/.well-known/openid-configuration">Discovery</a><a class="btn" href="/auth/login?redirect=/console">进入系统</a></div></div></nav>
  <main class="shell hero">
    <section class="copy">
      <p>统一身份认证服务</p>
      <h1>一次登录，连接 ChatGPT Team 与内部应用</h1>
      <div class="lead">轻量 OIDC Provider，支持账号注册、邀请码策略、管理员后台和 Redis 持久化，适合小团队快速接入自定义 SSO。</div>
      <div class="actions"><a class="btn" href="/auth/login?redirect=/console">用户登录</a><a class="btn secondary" href="/admin/login?redirect=/admin/console">管理员后台</a></div>
    </section>
    <aside class="glass status">
      <h2>服务状态</h2>
      <div class="metric"><strong>Issuer</strong><span>{issuer}</span></div>
      <div class="metric"><strong>Domains</strong><span>{domains}</span></div>
      <div class="metric"><strong>Protocol</strong><span>OIDC / OAuth 2.0 Authorization Code</span></div>
    </aside>
  </main>
  <section class="band"><div class="shell cards">
    <article class="glass card"><span class="icon">ID</span><h3>标准 OIDC</h3><p>提供 discovery、authorize、token、JWKS 和 RS256 ID Token。</p></article>
    <article class="glass card"><span class="icon">KV</span><h3>Redis 持久化</h3><p>用户、邀请码、策略和密钥可随 Render 重新部署保留。</p></article>
    <article class="glass card"><span class="icon">AD</span><h3>管理控制台</h3><p>集中管理注册策略、可用前缀、邀请码状态和用户活动。</p></article>
  </div></section>
</body>
</html>"""


def login_page(ns, query: dict, error=None, preview=False) -> str:
    hidden = "\n".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(str(v))}">'
        for k, v in query.items()
    )
    error_block = f'<p class="error">{_safe(error)}</p>' if error else ""
    domain_options = "\n".join(
        f'<option value="{_safe(domain)}">{_safe(domain)}</option>'
        for domain in (ns["EMAIL_DOMAINS"] or [ns["EMAIL_DOMAIN"]])
        if domain
    ) or '<option value="">not configured</option>'
    invite_required = bool(ns["app_settings"].get("invite_required", True))
    form_action = "/auth/login" if preview else "/authorize"
    notice = '<p class="notice">登录已有账号，或按当前注册策略创建账号。</p>' if preview else ""
    service = _brand(ns)
    bg = _bg(ns)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Login | {service}</title>
  <style>
    {_shell_css()}
    body {{ position:relative; background:linear-gradient(110deg, rgba(15,23,42,.72), rgba(37,99,235,.16), rgba(20,184,166,.18)), url("{bg}"); background-position:center; background-size:cover; background-attachment:fixed; overflow-x:hidden; }}
    body::before {{ content:""; position:fixed; inset:0; pointer-events:none; background:linear-gradient(90deg, rgba(255,255,255,.10) 1px, transparent 1px), linear-gradient(0deg, rgba(255,255,255,.08) 1px, transparent 1px); background-size:56px 56px; mask-image:linear-gradient(90deg, rgba(0,0,0,.72), transparent 66%); }}
    body::after {{ content:""; position:fixed; right:-20vw; bottom:-30vw; width:62vw; height:62vw; pointer-events:none; background:radial-gradient(circle, rgba(217,154,43,.24), transparent 62%); animation:softGlow 9s ease-in-out infinite; }}
    .page {{ position:relative; min-height:100vh; display:grid; grid-template-columns:minmax(0,1fr) minmax(340px,456px); gap:56px; align-items:center; padding:72px min(8vw,96px); }}
    .topbar {{ position:absolute; z-index:2; top:24px; left:min(8vw,96px); right:min(8vw,96px); display:flex; justify-content:space-between; color:#fff; }}
    .intro {{ color:#fff; text-shadow:0 18px 45px rgba(6,13,28,.36); animation:riseIn .58s ease both; }}
    .intro p {{ margin:0 0 14px; font-weight:900; }}
    .intro h1 {{ margin:0; font-size:52px; line-height:1.08; letter-spacing:0; }}
    .intro .lead {{ max-width:540px; margin-top:20px; color:rgba(255,255,255,.9); line-height:1.75; font-weight:700; }}
    .card {{ position:relative; padding:32px 36px; animation:riseIn .58s ease .08s both; overflow:hidden; }}
    .card::before {{ content:""; position:absolute; inset:0 0 auto; height:4px; background:linear-gradient(90deg, var(--blue), var(--mint), var(--gold)); }}
    .tabs {{ display:grid; grid-template-columns:repeat(3,1fr); margin:18px 0 16px; border-bottom:1px solid rgba(148,163,184,.24); }}
    .tab-button {{ min-height:42px; margin:0; color:#33455f; background:transparent; border:0; border-bottom:2px solid transparent; border-radius:0; font:inherit; font-weight:900; cursor:pointer; }}
    .tab-button.active {{ color:var(--blue); border-color:var(--blue); }}
    .register-only, .forgot-panel {{ display:none; }}
    body[data-mode="register"] .register-only {{ display:block; }}
    body[data-mode="forgot"] .login-form {{ display:none; }}
    body[data-mode="forgot"] .forgot-panel {{ display:block; }}
    .brand-row {{ display:flex; align-items:center; gap:12px; margin-bottom:22px; }}
    .brand-name {{ margin:0; font-size:17px; font-weight:900; }}
    .brand-meta {{ margin:3px 0 0; color:var(--soft); font-size:13px; font-weight:700; }}
    h2 {{ margin:0; font-size:28px; }}
    .lead-text {{ margin:10px 0 18px; color:var(--soft); line-height:1.65; }}
    .error, .notice {{ margin:0 0 14px; padding:10px 12px; border-radius:8px; font-size:13px; line-height:1.5; }}
    .error {{ color:#8a241f; background:rgba(254,226,226,.84); border:1px solid rgba(248,113,113,.30); }}
    .notice {{ color:#24384f; background:rgba(239,246,255,.82); border:1px solid rgba(96,165,250,.28); }}
    .toast {{ position:fixed; z-index:20; top:18px; left:50%; width:min(460px, calc(100% - 28px)); transform:translate(-50%, -14px); opacity:0; pointer-events:none; transition:opacity .18s ease, transform .18s ease; }}
    .toast.open {{ opacity:1; pointer-events:auto; transform:translate(-50%, 0); }}
    .toast-box {{ display:grid; grid-template-columns:1fr auto; gap:10px; padding:14px 16px; border-left:4px solid var(--gold); }}
    .toast strong {{ display:block; margin-bottom:3px; }}
    .toast p {{ margin:0; color:var(--soft); line-height:1.5; }}
    .toast-close {{ width:30px; height:30px; min-height:30px; padding:0; border-radius:8px; border:1px solid rgba(148,163,184,.38); color:var(--dark); background:rgba(255,255,255,.76); box-shadow:none; font-size:18px; line-height:1; }}
    .invite-inline {{ display:none; }}
    body[data-invite-visible="true"] .invite-inline {{ display:block; }}
    @media (max-width: 820px) {{ .page {{ grid-template-columns:1fr; gap:28px; padding:86px 16px 32px; }} .intro h1 {{ font-size:38px; }} .card {{ padding:26px; }} .topbar {{ left:16px; right:16px; top:18px; }} }}
  </style>
</head>
<body data-mode="login">
<div class="toast" id="inviteToast" role="status" aria-live="polite" aria-hidden="true">
  <div class="glass toast-box">
    <div><strong>注册需要邀请码</strong><p>请填写管理员发放的邀请码后再次提交注册。</p></div>
    <button class="toast-close" id="inviteToastClose" type="button" aria-label="关闭">×</button>
  </div>
</div>
<div class="page">
  <header class="topbar"><a class="brand" href="/"><span class="mark">SSO</span><span>{service}</span></a></header>
  <section class="intro"><p>欢迎回来</p><h1>继续你的统一身份认证流程</h1><div class="lead">登录已有账号，或在提交注册信息后按策略输入邀请码。</div></section>
  <main class="glass card">
    <div class="brand-row"><span class="mark">ID</span><div><p class="brand-name">{service}</p><p class="brand-meta">统一身份认证</p></div></div>
    <h2 id="formTitle">登录账号</h2>
    <p class="lead-text">请输入邮箱前缀、域名和账号密码继续。</p>
    {notice}{error_block}
    <nav class="tabs"><button class="tab-button active" type="button" data-mode-target="login">登录</button><button class="tab-button" type="button" data-mode-target="register">注册</button><button class="tab-button" type="button" data-mode-target="forgot">找回</button></nav>
    <form class="login-form" method="post" action="{form_action}">
      {hidden}
      <input type="hidden" id="modeField" name="mode" value="login">
      <div class="register-only"><label for="display_name">显示名称</label><input id="display_name" name="display_name" autocomplete="name" placeholder="Komorebi"></div>
      <label for="prefix">邮箱前缀</label><input id="prefix" name="prefix" autocomplete="username" placeholder="alice" required autofocus>
      <label for="domain">邮箱域名</label><select id="domain" name="domain" required>{domain_options}</select>
      <label for="password">账号密码</label><input id="password" name="password" type="password" autocomplete="current-password" placeholder="请输入账号密码" required>
      <div class="register-only invite-inline"><label for="invite_code">邀请码（选填）</label><input id="invite_code" name="invite_code" autocomplete="one-time-code" placeholder="INV-XXXXXXXXXX"></div>
      <button class="btn submit" id="submitButton" type="submit" style="width:100%; margin-top:18px">登录</button>
    </form>
    <a class="btn secondary" style="width:100%; margin-top:10px" href="/admin/login?redirect=/admin/console">进入管理后台</a>
    <section class="forgot-panel"><p class="notice">请联系管理员重置账号密码。</p></section>
  </main>
</div>
<script>
  const inviteRequired = {str(invite_required).lower()};
  const form = document.querySelector(".login-form");
  const modeField = document.getElementById("modeField");
  const title = document.getElementById("formTitle");
  const submit = document.getElementById("submitButton");
  const inviteInput = document.getElementById("invite_code");
  const toast = document.getElementById("inviteToast");
  const showInviteToast = () => {{ toast.classList.add("open"); toast.setAttribute("aria-hidden", "false"); document.body.dataset.inviteVisible = "true"; }};
  const hideInviteToast = () => {{ toast.classList.remove("open"); toast.setAttribute("aria-hidden", "true"); }};
  const setMode = (mode) => {{
    document.body.dataset.mode = mode; modeField.value = mode; delete document.body.dataset.inviteVisible; inviteInput.value = ""; hideInviteToast();
    title.textContent = mode === "register" ? "注册账号" : "登录账号";
    submit.textContent = mode === "register" ? "注册并继续" : "登录";
    document.querySelectorAll(".tab-button").forEach((button) => button.classList.toggle("active", button.dataset.modeTarget === mode));
  }};
  form.addEventListener("submit", (event) => {{
    if (document.body.dataset.mode === "register" && inviteRequired && !inviteInput.value.trim()) {{
      event.preventDefault();
      showInviteToast();
    }}
  }});
  document.getElementById("inviteToastClose").addEventListener("click", hideInviteToast);
  document.querySelectorAll(".tab-button").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.modeTarget)));
  setMode("login");
</script>
</body>
</html>"""


def admin_login_page(ns, error="", redirect="/console") -> str:
    service = _brand(ns)
    bg = _bg(ns)
    error_block = f'<p class="error">{_safe(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Admin | {service}</title><style>
{_shell_css()}
body {{ min-height:100vh; display:grid; place-items:center; padding:24px; background:linear-gradient(120deg, rgba(15,23,42,.68), rgba(37,99,235,.20)), url("{bg}"); background-position:center; background-size:cover; }}
main {{ width:min(440px,100%); padding:32px; }}
h1 {{ margin:0 0 8px; font-size:28px; }}
p {{ margin:0 0 20px; color:var(--soft); line-height:1.6; }}
.error {{ margin:0 0 14px; padding:10px 12px; border-radius:8px; color:#8a241f; background:rgba(254,226,226,.84); border:1px solid rgba(248,113,113,.30); }}
</style></head><body><main class="glass">
<a class="brand" href="/"><span class="mark">SSO</span><span>{service}</span></a>
<h1>管理员登录</h1><p>管理邀请码、注册策略、用户列表和持久化状态。</p>{error_block}
<form method="post" action="/admin/login"><input type="hidden" name="redirect" value="{_safe(redirect)}"><label for="username">管理员账号</label><input id="username" name="username" autocomplete="username" required autofocus><label for="password">管理员密码</label><input id="password" name="password" type="password" autocomplete="current-password" required><button class="btn" style="width:100%; margin-top:18px" type="submit">进入后台</button></form>
<a class="btn secondary" style="width:100%; margin-top:10px" href="/auth/login?redirect=/console">返回用户登录</a>
</main></body></html>"""


def admin_console_page(ns) -> str:
    fmt_time = ns["fmt_time"]
    invite_available = ns["invite_available"]
    invitations = ns["invitations"]
    profiles = ns["profiles"]
    service = _brand(ns)
    bg = _bg(ns)
    active_invites = sum(1 for item in invitations.values() if item.get("active", True) and invite_available(item.get("code", ""))[0])
    used_invites = sum(int(item.get("uses") or 0) for item in invitations.values())
    invite_rows = []
    for invite in sorted(invitations.values(), key=lambda item: int(item.get("created_at") or 0), reverse=True):
        raw_code = invite.get("code", "")
        code = _safe(raw_code)
        used_by = invite.get("used_by") or []
        last_used = "-"
        if used_by:
            last = used_by[-1]
            last_used = f"{_safe(last.get('email', '-'))}<br><small>{fmt_time(last.get('used_at'))}</small>"
        invite_rows.append(f"""<tr data-search="{_safe((raw_code + ' ' + (invite.get('note') or '')).lower())}"><td><code>{code}</code></td><td>{_safe(invite.get('note') or '-')}</td><td>{int(invite.get('uses') or 0)}/{int(invite.get('max_uses') or 1)}</td><td>{fmt_time(invite.get('expires_at'))}</td><td>{last_used}</td><td><span class="pill">{'启用' if invite.get('active', True) else '停用'}</span></td><td>{fmt_time(invite.get('created_at'))}</td><td><form method="post" action="/admin/invites/{code}/toggle"><button class="btn secondary" type="submit">{'停用' if invite.get('active', True) else '启用'}</button></form></td></tr>""")
    if not invite_rows:
        invite_rows.append('<tr><td colspan="8" class="empty">还没有邀请码，先生成一个。</td></tr>')
    user_rows = []
    for email, profile in sorted(profiles.items(), key=lambda item: int(item[1].get("registered_at") or 0), reverse=True):
        prefix = profile.get("prefix") or email.split("@", 1)[0]
        domain = email.split("@", 1)[1] if "@" in email else "-"
        user_rows.append(f"""<tr data-search="{_safe((email + ' ' + (profile.get('name') or '')).lower())}"><td>{_safe(email)}</td><td>{_safe(profile.get('name') or email)}</td><td><code>{_safe(prefix)}</code></td><td>{_safe(domain)}</td><td>{fmt_time(profile.get('registered_at'))}</td><td>{fmt_time(profile.get('last_login_at'))}</td></tr>""")
    if not user_rows:
        user_rows.append('<tr><td colspan="6" class="empty">暂无注册用户。</td></tr>')
    invite_checked = "checked" if ns["app_settings"].get("invite_required", True) else ""
    allow_any_checked = "checked" if ns["_runtime_allow_any_prefix"]() else ""
    allowed_prefixes_text = _safe(", ".join(sorted(ns["_runtime_allowed_prefixes"]())))
    backend_label = _safe(ns["state_backend"]().label)
    issuer = _safe(ns["ISSUER"] or "not configured")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Admin | {service}</title><style>
{_shell_css()}
body {{ background:linear-gradient(180deg, rgba(247,251,255,.92), rgba(228,238,247,.94)), url("{bg}"); background-position:center; background-size:cover; background-attachment:fixed; }}
.layout {{ padding:34px 0 64px; }}
h1 {{ margin:0 0 8px; font-size:38px; line-height:1.1; }}
.lead {{ margin:0 0 22px; color:var(--soft); font-weight:700; }}
.stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:18px; }}
.stat {{ position:relative; padding:18px; overflow:hidden; animation:riseIn .5s ease both; }}
.stat::before {{ content:""; position:absolute; inset:0 0 auto; height:3px; background:linear-gradient(90deg, var(--blue), var(--mint)); }}
.stat b {{ display:block; font-size:28px; }}
.stat span {{ color:#64748b; font-weight:800; }}
.grid {{ display:grid; grid-template-columns:360px minmax(0,1fr); gap:18px; align-items:start; }}
.stack {{ display:grid; gap:18px; }}
.panel {{ padding:22px; overflow:hidden; animation:riseIn .5s ease both; }}
.panel h2 {{ margin:0 0 14px; font-size:20px; }}
.check {{ display:flex; align-items:center; gap:10px; margin:0 0 12px; line-height:1.45; }}
.check input {{ width:18px; min-height:18px; }}
textarea {{ min-height:92px; resize:vertical; }}
.table-head {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:12px; }}
.search {{ max-width:280px; }}
small {{ color:#64748b; }}
tbody tr {{ transition:background .16s ease; }}
tbody tr:hover {{ background:rgba(37,99,235,.045); }}
@media (max-width:980px) {{ .stats, .grid {{ grid-template-columns:1fr; }} h1 {{ font-size:32px; }} .table-head {{ align-items:stretch; flex-direction:column; }} .search {{ max-width:none; }} }}
</style></head><body>
<nav class="nav"><div class="nav-inner"><a class="brand" href="/"><span class="mark">SSO</span><span>{service}</span></a><div class="actions"><a class="btn secondary" href="/">首页</a><form method="post" action="/admin/logout"><button class="btn secondary" type="submit">退出</button></form></div></div></nav>
<main class="shell layout">
<h1>管理员后台</h1><p class="lead">管理注册策略、邀请码、用户列表和持久化状态。</p>
<section class="stats"><div class="glass stat"><b>{len(profiles)}</b><span>注册用户</span></div><div class="glass stat"><b>{len(invitations)}</b><span>邀请码</span></div><div class="glass stat"><b>{active_invites}</b><span>可用邀请码</span></div><div class="glass stat"><b>{used_invites}</b><span>累计使用</span></div></section>
<section class="grid"><div class="stack">
<section class="glass panel"><h2>注册策略</h2><form method="post" action="/admin/settings"><label class="check"><input type="checkbox" name="invite_required" value="on" {invite_checked}> 注册时必须填写邀请码</label><label class="check"><input type="checkbox" name="allow_any_prefix" value="on" {allow_any_checked}> 允许任意邮箱前缀注册</label><label for="allowed_prefixes">允许的邮箱前缀</label><textarea id="allowed_prefixes" name="allowed_prefixes" placeholder="alice, bob, charlie">{allowed_prefixes_text}</textarea><p class="muted">关闭任意前缀后，只允许这里列出的前缀注册或登录。</p><button class="btn" style="width:100%" type="submit">保存策略</button></form></section>
<section class="glass panel"><h2>生成邀请码</h2><form method="post" action="/admin/invites"><label for="note">备注</label><input id="note" name="note" placeholder="例如：6 月新用户"><label for="max_uses">可用次数</label><input id="max_uses" name="max_uses" type="number" min="1" max="999" value="1"><label for="expires_days">有效天数</label><input id="expires_days" name="expires_days" type="number" min="0" max="365" value="7"><button class="btn" style="width:100%; margin-top:16px" type="submit">生成邀请码</button></form></section>
<section class="glass panel"><h2>系统状态</h2><p class="muted">存储后端：<code>{backend_label}</code></p><p class="muted">Issuer：<code>{issuer}</code></p><p class="muted">Discovery：<code>/.well-known/openid-configuration</code></p></section>
</div><div class="stack">
<section class="glass panel"><div class="table-head"><h2>邀请码</h2><input class="search" data-filter="invite-table" placeholder="搜索邀请码或备注"></div><table id="invite-table"><thead><tr><th>邀请码</th><th>备注</th><th>使用</th><th>过期</th><th>最近使用</th><th>状态</th><th>创建</th><th>操作</th></tr></thead><tbody>{''.join(invite_rows)}</tbody></table></section>
<section class="glass panel"><div class="table-head"><h2>用户</h2><input class="search" data-filter="user-table" placeholder="搜索邮箱或名称"></div><table id="user-table"><thead><tr><th>邮箱</th><th>显示名称</th><th>前缀</th><th>域名</th><th>注册时间</th><th>最后登录</th></tr></thead><tbody>{''.join(user_rows)}</tbody></table></section>
</div></section></main>
<script>document.querySelectorAll("[data-filter]").forEach((input) => input.addEventListener("input", () => {{ const needle = input.value.trim().toLowerCase(); document.querySelectorAll("#" + input.dataset.filter + " tbody tr").forEach((row) => {{ const text = row.dataset.search || row.textContent.toLowerCase(); row.style.display = text.includes(needle) ? "" : "none"; }}); }}));</script>
</body></html>"""


def user_console_page(ns, email, profile) -> str:
    fmt_time = ns["fmt_time"]
    service = _brand(ns)
    bg = _bg(ns)
    display_name = _safe(profile.get("name") or email)
    safe_email = _safe(email)
    initials = _safe((profile.get("name") or email)[:2].upper())
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Console | {service}</title><style>
{_shell_css()}
body {{ background:linear-gradient(180deg, rgba(247,251,255,.90), rgba(228,238,247,.94)), url("{bg}"); background-position:center; background-size:cover; background-attachment:fixed; }}
.shell {{ padding:36px 0 70px; }}
.welcome {{ display:grid; grid-template-columns:minmax(0,1fr) 310px; gap:18px; align-items:stretch; margin-bottom:18px; }}
.hero {{ position:relative; padding:30px; color:#fff; background:linear-gradient(120deg, rgba(23,32,51,.90), rgba(37,99,235,.70), rgba(19,163,141,.45)), url("{bg}"); background-position:center; background-size:cover; overflow:hidden; animation:riseIn .52s ease both; }}
.hero::after {{ content:""; position:absolute; inset:auto 24px 0 24px; height:3px; background:linear-gradient(90deg, rgba(255,255,255,.0), rgba(255,255,255,.68), rgba(255,255,255,.0)); }}
.hero p {{ margin:0 0 10px; color:rgba(255,255,255,.86); font-weight:900; }}
h1 {{ margin:0; font-size:38px; line-height:1.12; letter-spacing:0; }}
.hero .lead {{ max-width:620px; margin-top:16px; color:rgba(255,255,255,.88); line-height:1.7; font-weight:700; }}
.avatar {{ display:grid; place-items:center; width:42px; height:42px; border-radius:8px; color:#fff; background:var(--blue); font-weight:900; }}
.identity {{ padding:24px; animation:riseIn .52s ease .06s both; }}
.identity .avatar {{ width:56px; height:56px; margin-bottom:14px; font-size:18px; }}
.identity strong, .identity span {{ display:block; overflow-wrap:anywhere; }}
.identity span {{ margin-top:6px; color:var(--soft); font-weight:700; }}
.stats {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-bottom:18px; }}
.metric {{ position:relative; padding:22px; color:var(--soft); font-weight:800; overflow:hidden; animation:riseIn .52s ease both; }}
.metric::before {{ content:""; position:absolute; inset:0 0 auto; height:3px; background:linear-gradient(90deg, var(--mint), var(--blue)); }}
.metric b {{ display:block; margin-bottom:8px; color:var(--ink); font-size:24px; }}
.main-grid {{ display:grid; grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr); gap:18px; align-items:start; }}
.panel {{ padding:22px; animation:riseIn .52s ease both; }}
.apps {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
.app-card {{ display:flex; align-items:center; gap:14px; min-height:104px; padding:18px; transition:transform .18s ease, box-shadow .18s ease; }}
.app-card:hover {{ transform:translateY(-2px); box-shadow:0 24px 58px rgba(31,46,71,.14); }}
.app-icon {{ display:grid; place-items:center; flex:0 0 44px; width:44px; height:44px; border-radius:8px; color:#fff; background:var(--teal); font-weight:900; }}
.activity div {{ padding:13px 0; border-bottom:1px solid var(--line); }}
@media (max-width:900px) {{ .welcome, .main-grid, .stats, .apps {{ grid-template-columns:1fr; }} h1 {{ font-size:31px; }} }}
</style></head><body>
<nav class="nav"><div class="nav-inner"><a class="brand" href="/"><span class="mark">SSO</span><span>{service}</span></a><div class="actions"><span class="avatar">{initials}</span><a class="btn secondary" href="/">首页</a></div></div></nav>
<main class="shell">
<section class="welcome"><div class="glass hero"><p>个人工作台</p><h1>欢迎回来，{display_name}</h1><div class="lead">查看账号状态、访问已授权应用，并确认最近登录情况。</div></div><aside class="glass identity"><span class="avatar">{initials}</span><strong>{safe_email}</strong><span>已通过统一身份认证</span></aside></section>
<section class="stats"><div class="glass metric"><b>正常</b>账号状态</div><div class="glass metric"><b>{fmt_time(profile.get('registered_at'))}</b>注册时间</div><div class="glass metric"><b>{fmt_time(profile.get('last_login_at'))}</b>最近登录</div></section>
<section class="main-grid"><div class="glass panel"><h2>我的应用</h2><div class="apps"><a class="glass app-card" href="/"><span class="app-icon">ID</span><span><strong>统一认证</strong><br><span class="muted">返回服务首页</span></span></a><div class="glass app-card"><span class="app-icon">AI</span><span><strong>ChatGPT Team</strong><br><span class="muted">使用已授权身份继续访问</span></span></div><div class="glass app-card"><span class="app-icon">API</span><span><strong>开发者服务</strong><br><span class="muted">账号信息可用于 OIDC</span></span></div><div class="glass app-card"><span class="app-icon">ME</span><span><strong>个人资料</strong><br><code>{safe_email}</code></span></div></div></div><aside class="glass panel"><h2>最近动态</h2><div class="activity"><div><strong>登录成功</strong><br><span class="muted">{fmt_time(profile.get('last_login_at'))}</span></div><div><strong>身份已验证</strong><br><span class="muted">邮箱和域名策略校验通过</span></div><div><strong>安全建议</strong><br><span class="muted">请妥善保管账号密码，必要时联系管理员处理。</span></div></div></aside></section>
</main></body></html>"""


def install(ns):
    ns["root_page"] = lambda: root_page(ns)
    ns["html_page"] = lambda query, error=None, preview=False: login_page(ns, query, error, preview)
    ns["render_admin_login"] = lambda error="", redirect="/console": admin_login_page(ns, error, redirect)
    ns["render_admin_console"] = lambda: admin_console_page(ns)
    ns["render_user_console"] = lambda email, profile: user_console_page(ns, email, profile)
