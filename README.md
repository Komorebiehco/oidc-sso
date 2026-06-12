# Render ChatGPT OIDC SSO IdP

这是一个可部署在 Render 上的最小 OIDC Identity Provider。用户在 ChatGPT 登录页输入工作邮箱后，ChatGPT 会跳转到本服务的登录页：

```text
https://your-service.onrender.com/auth/login?uid=<oidc-interaction>&redirect=/console
```

用户完成邮箱 magic link 验证后，本服务会通过 OIDC 把用户跳转回 ChatGPT 完成登录。

> 适合 MVP / PoC。生产环境建议把内存存储替换为 Redis/Postgres，并接入企业邮箱、企业目录或正式 IdP。

## 真实登录流程

1. 用户打开 `chatgpt.com`。
2. 用户输入已验证域名下的工作邮箱。
3. ChatGPT 根据你的 Business/Enterprise SSO 配置，跳转到本服务的 OIDC authorization endpoint。
4. 本服务展示 `/auth/login?uid=...&redirect=/console` 登录页。
5. 用户输入域名邮箱。
6. 本服务发送一次性 magic link。
7. 用户点击 `/auth/magic/:token`。
8. 本服务完成 OIDC interaction，浏览器被重定向回 ChatGPT 的 callback/redirect URI。
9. ChatGPT 用 authorization code 换 token，并读取 `sub`, `email`, `email_verified`, `name` claims。

注意：`/auth/login?redirect=/console` 只是 IdP 自己的登录页样式/入口。真正让 ChatGPT 回来的不是这个 `redirect` 参数，而是 OIDC 授权请求里的 `redirect_uri`。

## 本地运行

```bash
cp .env.example .env
npm install
npm run dev
```

打开发现文档：

```text
http://localhost:3000/.well-known/openid-configuration
```

直接打开 `/auth/login?redirect=/console` 会提示必须从 ChatGPT/OIDC 流程发起，因为它缺少 OIDC interaction uid。

## Render 部署

1. 把项目推送到 GitHub。
2. 在 Render 创建 Web Service。
3. Build Command: `npm install`
4. Start Command: `npm start`
5. Health Check Path: `/healthz`
6. 设置环境变量。

## 必填环境变量

```text
NODE_ENV=production
ISSUER_URL=https://your-service.onrender.com
ALLOWED_EMAIL_DOMAIN=example.com
COOKIE_KEYS=long-random-secret-1,long-random-secret-2
OIDC_CLIENT_ID=chatgpt-business
OIDC_CLIENT_SECRET=long-random-client-secret
OIDC_REDIRECT_URIS=<ChatGPT 向导里给你的 OIDC callback/redirect URI>
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

先完成域名验证，然后选择自定义 OIDC。填入：

```text
Issuer / Discovery URL: https://your-service.onrender.com/.well-known/openid-configuration
Client ID: 与 OIDC_CLIENT_ID 相同
Client Secret: 与 OIDC_CLIENT_SECRET 相同
Scopes: openid email profile
```

然后把 ChatGPT 向导提供的 redirect/callback URI 填到 Render 的 `OIDC_REDIRECT_URIS`。

## 生产注意事项

- 本项目默认使用内存存储，Render 重启后 magic link、OIDC interaction、用户会话会失效。
- 多实例部署必须接 Redis/Postgres adapter。
- 建议开启 SMTP，禁止测试模式暴露 magic link。
- 建议把 `ALLOWED_EMAIL_DOMAIN` 固定为公司域名。
- 建议保留一个外部域名后门管理员账号，避免 SSO 配置错误后无法进入工作空间。
