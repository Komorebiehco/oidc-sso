import 'dotenv/config';
import Koa from 'koa';
import Router from '@koa/router';
import mount from 'koa-mount';
import bodyParser from 'koa-bodyparser';
import session from 'koa-session';
import Provider from 'oidc-provider';
import nodemailer from 'nodemailer';
import { randomBytes, createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import ejs from 'ejs';

const __dirname = dirname(fileURLToPath(import.meta.url));

const PORT = Number(process.env.PORT || 3000);
const ISSUER_URL = (process.env.ISSUER_URL || `http://localhost:${PORT}`).replace(/\/$/, '');
const ALLOWED_EMAIL_DOMAIN = (process.env.ALLOWED_EMAIL_DOMAIN || '').toLowerCase().trim();
const SUPPORT_EMAIL = process.env.SUPPORT_EMAIL || '';
const OIDC_CLIENT_ID = process.env.OIDC_CLIENT_ID || 'chatgpt-business';
const OIDC_CLIENT_SECRET = process.env.OIDC_CLIENT_SECRET || 'dev-secret-change-me';
const OIDC_REDIRECT_URIS = (process.env.OIDC_REDIRECT_URIS || 'http://localhost:3001/callback')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);
const COOKIE_KEYS = (process.env.COOKIE_KEYS || 'dev-key-1,dev-key-2').split(',').map((s) => s.trim());
const PUBLIC_CONSOLE_PATH = process.env.PUBLIC_CONSOLE_PATH || '/console';

// Demo in-memory stores. For production, replace with Redis/Postgres adapter.
const users = new Map(); // accountId -> { accountId, email, name }
const magicTokens = new Map(); // tokenHash -> { email, uid, expiresAt }

function normalizeEmail(email) {
  return String(email || '').trim().toLowerCase();
}

function emailAllowed(email) {
  if (!ALLOWED_EMAIL_DOMAIN) return true;
  return normalizeEmail(email).endsWith(`@${ALLOWED_EMAIL_DOMAIN}`);
}

function accountIdFromEmail(email) {
  return createHash('sha256').update(normalizeEmail(email)).digest('hex');
}

function tokenHash(token) {
  return createHash('sha256').update(token).digest('hex');
}

function getUserByEmail(email) {
  const normalized = normalizeEmail(email);
  const accountId = accountIdFromEmail(normalized);
  if (!users.has(accountId)) {
    users.set(accountId, {
      accountId,
      email: normalized,
      name: normalized.split('@')[0],
    });
  }
  return users.get(accountId);
}

async function render(view, locals = {}) {
  return ejs.renderFile(join(__dirname, 'views', view), locals);
}

async function sendMagicLink(email, url) {
  const host = process.env.SMTP_HOST;
  if (!host) return { sent: false, previewUrl: url };

  const transporter = nodemailer.createTransport({
    host,
    port: Number(process.env.SMTP_PORT || 587),
    secure: String(process.env.SMTP_SECURE || 'false') === 'true',
    auth: process.env.SMTP_USER ? {
      user: process.env.SMTP_USER,
      pass: process.env.SMTP_PASS,
    } : undefined,
  });

  await transporter.sendMail({
    from: process.env.SMTP_FROM || 'ChatGPT SSO <no-reply@example.com>',
    to: email,
    subject: 'Your ChatGPT SSO sign-in link',
    text: `Use this link to sign in to ChatGPT. It expires in 10 minutes:\n\n${url}`,
    html: `<p>Use this link to sign in to ChatGPT. It expires in 10 minutes.</p><p><a href="${url}">Sign in to ChatGPT</a></p>`,
  });
  return { sent: true };
}

const oidc = new Provider(ISSUER_URL, {
  clients: [{
    client_id: OIDC_CLIENT_ID,
    client_secret: OIDC_CLIENT_SECRET,
    redirect_uris: OIDC_REDIRECT_URIS,
    response_types: ['code'],
    grant_types: ['authorization_code'],
    token_endpoint_auth_method: 'client_secret_basic',
  }],
  claims: {
    openid: ['sub'],
    email: ['email', 'email_verified'],
    profile: ['name'],
  },
  scopes: ['openid', 'email', 'profile'],
  features: {
    devInteractions: { enabled: false },
    rpInitiatedLogout: { enabled: true },
  },
  cookies: {
    keys: COOKIE_KEYS,
    long: { signed: true, secure: process.env.NODE_ENV === 'production' },
    short: { signed: true, secure: process.env.NODE_ENV === 'production' },
  },
  async findAccount(ctx, id) {
    const user = users.get(id);
    if (!user) return undefined;
    return {
      accountId: id,
      async claims() {
        return {
          sub: id,
          email: user.email,
          email_verified: true,
          name: user.name,
        };
      },
    };
  },
  interactions: {
    url(ctx, interaction) {
      // ChatGPT starts at the OIDC authorization endpoint. We then show a branded
      // login page that looks like /auth/login?redirect=/console while retaining
      // the OIDC interaction uid needed to finish back to ChatGPT.
      return `/auth/login?uid=${encodeURIComponent(interaction.uid)}&redirect=${encodeURIComponent(PUBLIC_CONSOLE_PATH)}`;
    },
  },
});

oidc.proxy = true;

const app = new Koa();
app.keys = COOKIE_KEYS;
app.proxy = true;
app.use(bodyParser());
app.use(session({ key: 'sso.sid', maxAge: 10 * 60 * 1000, httpOnly: true, sameSite: 'lax' }, app));

const router = new Router();

router.get('/healthz', (ctx) => {
  ctx.body = 'ok';
});

router.get('/', async (ctx) => {
  ctx.type = 'html';
  ctx.body = await render('home.ejs', {
    issuer: ISSUER_URL,
    domain: ALLOWED_EMAIL_DOMAIN || 'any domain',
    clientId: OIDC_CLIENT_ID,
    redirectUris: OIDC_REDIRECT_URIS,
  });
});

router.get('/auth/login', async (ctx) => {
  const uid = String(ctx.query.uid || '');
  const redirect = String(ctx.query.redirect || PUBLIC_CONSOLE_PATH);

  if (!uid) {
    ctx.status = 400;
    ctx.type = 'html';
    ctx.body = await render('message.ejs', {
      title: '请从 ChatGPT 发起登录',
      message: '这个页面需要由 ChatGPT 的 SSO 流程跳转进入。用户应先在 chatgpt.com 输入工作邮箱，然后再跳转到这里登录。',
    });
    return;
  }

  // Validate that this uid belongs to an active OIDC interaction.
  await oidc.interactionDetails(ctx.req, ctx.res);

  ctx.type = 'html';
  ctx.body = await render('login.ejs', {
    uid,
    redirect,
    domain: ALLOWED_EMAIL_DOMAIN,
    supportEmail: SUPPORT_EMAIL,
    error: ctx.query.error || '',
    sent: false,
    previewUrl: '',
  });
});

router.post('/auth/login', async (ctx) => {
  const uid = String(ctx.request.body.uid || '');
  const redirect = String(ctx.request.body.redirect || PUBLIC_CONSOLE_PATH);
  const email = normalizeEmail(ctx.request.body.email);

  if (!uid) {
    ctx.redirect(`/auth/login?error=${encodeURIComponent('登录会话无效，请重新从 ChatGPT 发起登录')}`);
    return;
  }
  if (!email || !email.includes('@')) {
    ctx.redirect(`/auth/login?uid=${encodeURIComponent(uid)}&redirect=${encodeURIComponent(redirect)}&error=${encodeURIComponent('请输入有效邮箱地址')}`);
    return;
  }
  if (!emailAllowed(email)) {
    ctx.redirect(`/auth/login?uid=${encodeURIComponent(uid)}&redirect=${encodeURIComponent(redirect)}&error=${encodeURIComponent(`只允许 @${ALLOWED_EMAIL_DOMAIN} 邮箱登录`)}`);
    return;
  }

  // Ensure this uid is a valid current OIDC interaction before issuing a link.
  await oidc.interactionDetails(ctx.req, ctx.res);

  const token = randomBytes(32).toString('base64url');
  magicTokens.set(tokenHash(token), {
    email,
    uid,
    expiresAt: Date.now() + 10 * 60 * 1000,
  });
  const url = `${ISSUER_URL}/auth/magic/${token}`;
  const result = await sendMagicLink(email, url);

  ctx.type = 'html';
  ctx.body = await render('login.ejs', {
    uid,
    redirect,
    domain: ALLOWED_EMAIL_DOMAIN,
    supportEmail: SUPPORT_EMAIL,
    error: '',
    sent: true,
    previewUrl: result.previewUrl || '',
  });
});

router.get('/auth/magic/:token', async (ctx) => {
  const { token } = ctx.params;
  const key = tokenHash(token);
  const record = magicTokens.get(key);
  magicTokens.delete(key);

  if (!record || record.expiresAt < Date.now()) {
    ctx.status = 400;
    ctx.type = 'html';
    ctx.body = await render('message.ejs', {
      title: '登录链接无效',
      message: '链接已过期或已经使用过。请重新从 ChatGPT 输入工作邮箱并发起 SSO 登录。',
    });
    return;
  }

  // This completes the OIDC interaction. oidc-provider then redirects the browser
  // back to ChatGPT's redirect_uri with an authorization code.
  const user = getUserByEmail(record.email);
  const result = {
    login: {
      accountId: user.accountId,
      remember: false,
      ts: Math.floor(Date.now() / 1000),
    },
    consent: {},
  };

  await oidc.interactionFinished(ctx.req, ctx.res, result, { mergeWithLastSubmission: false });
});

app.use(router.routes()).use(router.allowedMethods());
app.use(mount(oidc.app));

app.listen(PORT, () => {
  console.log(`OIDC SSO IdP listening on ${ISSUER_URL}`);
});
