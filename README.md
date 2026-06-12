# Render SSO Google OIDC Demo

一个可以部署到 Render 的最小 SSO 登录程序，使用 Node.js、Express、Google OAuth 2.0 / OpenID Connect。

## 本地运行

```bash
npm install
cp .env.example .env
npm run dev
```

然后打开：

```text
http://localhost:3000
```

## Google Cloud Console 配置

创建 OAuth Client ID，类型选择 Web application。

本地开发时添加 Authorized redirect URI：

```text
http://localhost:3000/auth/google/callback
```

Render 部署后添加：

```text
https://你的-render域名.onrender.com/auth/google/callback
```

## Render 部署

1. 把本项目推送到 GitHub。
2. 在 Render 创建 Web Service，连接该仓库。
3. Build Command 使用：`npm install`
4. Start Command 使用：`npm start`
5. 添加环境变量：

| 环境变量 | 说明 |
| --- | --- |
| `NODE_ENV` | `production` |
| `BASE_URL` | 你的 Render 服务地址，例如 `https://xxx.onrender.com` |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth Client Secret |
| `SESSION_SECRET` | 随机长字符串 |
| `ALLOWED_EMAIL_DOMAIN` | 可选，只允许某个邮箱域名登录，例如 `example.com` |

## 路由

- `/` 首页
- `/login` 开始 Google 登录
- `/auth/google/callback` Google 回调
- `/dashboard` 登录后页面
- `/logout` 退出登录
- `/healthz` Render 健康检查

## 生产建议

当前示例使用内存 session，适合演示或单实例服务。正式生产建议改成 Redis/Postgres session store。
