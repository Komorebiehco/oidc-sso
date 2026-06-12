import 'dotenv/config';
import crypto from 'crypto';
import express from 'express';
import session from 'express-session';
import helmet from 'helmet';
import { OAuth2Client } from 'google-auth-library';

const {
  PORT = 3000,
  BASE_URL,
  GOOGLE_CLIENT_ID,
  GOOGLE_CLIENT_SECRET,
  SESSION_SECRET,
  ALLOWED_EMAIL_DOMAIN,
  NODE_ENV = 'development'
} = process.env;

for (const [key, value] of Object.entries({ BASE_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, SESSION_SECRET })) {
  if (!value) {
    throw new Error(`Missing required environment variable: ${key}`);
  }
}

const isProduction = NODE_ENV === 'production';
const app = express();
app.set('trust proxy', 1);
app.use(helmet());
app.use(express.urlencoded({ extended: false }));
app.use(express.json());

app.use(
  session({
    name: 'sso.sid',
    secret: SESSION_SECRET,
    resave: false,
    saveUninitialized: false,
    cookie: {
      httpOnly: true,
      sameSite: 'lax',
      secure: isProduction,
      maxAge: 1000 * 60 * 60 * 8
    }
  })
);

const redirectUri = `${BASE_URL.replace(/\/$/, '')}/auth/google/callback`;
const oauth2Client = new OAuth2Client(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, redirectUri);

function html(title, body) {
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${escapeHtml(title)}</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 0; background: #f6f7f9; color: #111827; }
    main { max-width: 720px; margin: 10vh auto; background: white; padding: 32px; border-radius: 16px; box-shadow: 0 10px 35px rgba(0,0,0,.08); }
    a.button, button { display: inline-block; padding: 12px 16px; border-radius: 10px; border: 0; background: #111827; color: white; text-decoration: none; cursor: pointer; }
    pre { background: #f3f4f6; padding: 16px; overflow: auto; border-radius: 10px; }
    .muted { color: #6b7280; }
  </style>
</head>
<body><main>${body}</main></body>
</html>`;
}

function escapeHtml(value = '') {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function requireAuth(req, res, next) {
  if (req.session.user) return next();
  req.session.returnTo = req.originalUrl;
  return res.redirect('/login');
}

app.get('/healthz', (_req, res) => res.status(200).send('ok'));

app.get('/', (req, res) => {
  if (req.session.user) return res.redirect('/dashboard');
  res.send(
    html(
      'SSO 登录',
      `<h1>SSO 登录示例</h1>
       <p class="muted">使用 Google OAuth/OIDC 登录，适合部署到 Render。</p>
       <p><a class="button" href="/login">使用 Google 登录</a></p>`
    )
  );
});

app.get('/login', (req, res) => {
  const state = crypto.randomBytes(24).toString('hex');
  req.session.oauthState = state;

  const url = oauth2Client.generateAuthUrl({
    access_type: 'offline',
    scope: ['openid', 'email', 'profile'],
    prompt: 'select_account',
    state
  });

  res.redirect(url);
});

app.get('/auth/google/callback', async (req, res, next) => {
  try {
    const { code, state } = req.query;
    if (!code || !state || state !== req.session.oauthState) {
      return res.status(400).send('Invalid OAuth state. Please try logging in again.');
    }
    delete req.session.oauthState;

    const { tokens } = await oauth2Client.getToken(String(code));
    if (!tokens.id_token) {
      return res.status(401).send('Google did not return an ID token.');
    }

    const ticket = await oauth2Client.verifyIdToken({
      idToken: tokens.id_token,
      audience: GOOGLE_CLIENT_ID
    });
    const payload = ticket.getPayload();

    const email = payload?.email;
    const emailVerified = payload?.email_verified;
    if (!email || !emailVerified) {
      return res.status(403).send('Email is missing or not verified.');
    }

    if (ALLOWED_EMAIL_DOMAIN && !email.toLowerCase().endsWith(`@${ALLOWED_EMAIL_DOMAIN.toLowerCase()}`)) {
      return res.status(403).send(`Only @${escapeHtml(ALLOWED_EMAIL_DOMAIN)} accounts are allowed.`);
    }

    req.session.user = {
      sub: payload.sub,
      email,
      name: payload.name,
      picture: payload.picture
    };

    const returnTo = req.session.returnTo || '/dashboard';
    delete req.session.returnTo;
    res.redirect(returnTo);
  } catch (err) {
    next(err);
  }
});

app.get('/dashboard', requireAuth, (req, res) => {
  const user = req.session.user;
  res.send(
    html(
      'Dashboard',
      `<h1>登录成功</h1>
       <p>你好，<strong>${escapeHtml(user.name || user.email)}</strong></p>
       ${user.picture ? `<img src="${escapeHtml(user.picture)}" alt="avatar" width="72" height="72" style="border-radius:50%" />` : ''}
       <pre>${escapeHtml(JSON.stringify(user, null, 2))}</pre>
       <form method="post" action="/logout"><button type="submit">退出登录</button></form>`
    )
  );
});

app.post('/logout', (req, res, next) => {
  req.session.destroy(err => {
    if (err) return next(err);
    res.clearCookie('sso.sid');
    res.redirect('/');
  });
});

app.use((err, _req, res, _next) => {
  console.error(err);
  res.status(500).send(isProduction ? 'Internal Server Error' : `<pre>${escapeHtml(err.stack || err.message)}</pre>`);
});

app.listen(Number(PORT), () => {
  console.log(`SSO app listening on port ${PORT}`);
  console.log(`OAuth redirect URI: ${redirectUri}`);
});
