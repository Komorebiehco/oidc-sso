# Render ChatGPT OIDC SSO IdP

这是一个可部署在 Render 上的最小 OIDC Identity Provider，用公司域名邮箱 magic link 登录后，把用户通过 OIDC 登录到 ChatGPT Business（原 ChatGPT Team）。

> 适合 MVP / PoC。生产环境建议把内存存储替换为 Redis/Postgres，并接入企业邮箱或正式 IdP。

## 工作方式

1. 用户在 ChatGPT 登录页选择你的 Business 工作空间 SSO。
2. ChatGPT 作为 OIDC client 跳转到本服务。
3. 本服务要求用户输入指定域名邮箱，例如 `@example.com`。
4. 用户收到一次性 magic link。
5. 点击链接后，本服务向 ChatGPT 返回 OIDC authorization code。
6. ChatGPT 使用 code 换 token，并读取 `sub`, `email`, `email_verified`, `name` claims。

## 本地运行

```bash
cp .env.example .env
npm install
npm run dev
```

打开：

```text
http://localhost:3000/.well-known/openid-configuration
```

## Render 部署

1. 把项目推送到 GitHub。
2. 在 Render 创建 Web Service。
3. Build Command: `npm install`
4. Start Command: `npm start`
5. Health Check Path: `/healthz`
6. 设置环境变量。

## 必填环境变量

```text
ISSUER_URL=https://your-service.onrender.com
ALLOWED_EMAIL_DOMAIN=example.com
COOKIE_KEYS=long-random-secret-1,long-random-secret-2
OIDC_CLIENT_ID=chatgpt-business
OIDC_CLIENT_SECRET=long-random-client-secret
OIDC_REDIRECT_URIS=<ChatGPT 提供或要求配置的 OIDC callback/redirect URI>
```

## SMTP 环境变量

不配置 SMTP 时，系统会把 magic link 显示在网页上，仅方便测试。

```text
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_SECURE=false
SMTP_USER=smtp-user
SMTP_PASS=smtp-pass
SMTP_FROM="ChatGPT SSO <no-reply@example.com>"
```

## 在 ChatGPT Business 中配置

进入 ChatGPT Business 管理设置中的 Identity / Identity & Provisioning / SSO 配置区域。

先完成域名验证，然后选择自定义 OIDC（如果界面提供该选项）。填入：

```text
Issuer / Discovery URL: https://your-service.onrender.com/.well-known/openid-configuration
Client ID: 与 OIDC_CLIENT_ID 相同
Client Secret: 与 OIDC_CLIENT_SECRET 相同
Scopes: openid email profile
```

然后把 ChatGPT 要求的 redirect/callback URI 填到 Render 的 `OIDC_REDIRECT_URIS`。

## 生产注意事项

- 本项目默认使用内存存储，Render 重启后 magic link、登录会话会失效。
- 多实例部署必须接 Redis/Postgres adapter。
- 建议开启 SMTP，禁止测试模式暴露 magic link。
- 建议把 `ALLOWED_EMAIL_DOMAIN` 固定为公司域名。
- 建议保留一个外部域名的后门管理员账号，避免 SSO 配置错误后无法进入工作空间。
