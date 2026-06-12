# ChatGPT Team OIDC SSO for Render

这是一个可以部署到 Render 的最小 OIDC Provider，用于让 ChatGPT Team 通过自定义 SSO / OIDC 登录。

登录方式很简单：

1. 用户在 ChatGPT 里发起 SSO 登录。
2. ChatGPT 跳转到本服务的 `/authorize`。
3. 用户输入邮箱前缀和共享密码，例如输入 `alice` 后签发 `alice@example.com`。
4. 本服务把用户带回 ChatGPT，并返回带 `email`、`email_verified`、`name` 等声明的 `id_token`。

重要提醒：这是轻量共享密码方案，适合小团队、临时内部使用或测试。它不是企业级身份系统。生产环境更建议使用 Google Workspace、Microsoft Entra ID、Okta、Cloudflare Access 等成熟 IdP，并启用 MFA、审计和账号生命周期管理。

## Render 部署

### 方式 A：Blueprint

1. 把本仓库推送到你的 GitHub。
2. 在 Render 选择 **New > Blueprint**。
3. 选择这个仓库，Render 会读取 `render.yaml`。
4. 填写所有 `sync: false` 的环境变量。
5. 第一次部署完成后，把 `ISSUER` 设置成 Render 分配的公网地址，例如：

```text
https://your-service.onrender.com
```

然后重新部署一次。

### 方式 B：Web Service

1. 在 Render 选择 **New > Web Service**。
2. 连接 GitHub 仓库。
3. Runtime 选择 **Docker**。
4. Health Check Path 填：

```text
/healthz
```

5. 添加下面的环境变量。

## 环境变量

```env
ISSUER=https://your-service.onrender.com
OIDC_CLIENT_ID=chatgpt-team
OIDC_CLIENT_SECRET=replace-with-a-long-random-secret
OIDC_REDIRECT_URI=paste-the-exact-chatgpt-sso-callback-url-here
EMAIL_DOMAIN=example.com,example.org
SHARED_PASSWORD=replace-with-your-shared-login-password
ALLOW_ANY_PREFIX=true
ALLOWED_PREFIXES=
TOKEN_TTL_SECONDS=300
OIDC_PRIVATE_KEY_B64=paste-generated-value-here
OIDC_KEY_ID=render-sso-key-1
```

说明：

- `ISSUER` 必须是 Render 的 HTTPS 公网地址，末尾不要带 `/`。
- `OIDC_CLIENT_ID` 和 `OIDC_CLIENT_SECRET` 要和 ChatGPT Team SSO 配置中填写的一致。
- `OIDC_REDIRECT_URI` 必须和 ChatGPT Team SSO 配置页面显示的 callback / redirect URL 完全一致。
- `EMAIL_DOMAIN` 应该是你在 ChatGPT Team 工作区中验证并使用的邮箱域名；多个域名用英文逗号分隔。
- `ALLOW_ANY_PREFIX=true` 表示任意合法前缀都能用共享密码登录，例如 `alice@example.com`。
- 如果要限制用户，设置 `ALLOW_ANY_PREFIX=false`，并配置 `ALLOWED_PREFIXES=alice,bob,charlie`。

## 生成签名私钥

Render 的无持久磁盘文件系统不适合保存 OIDC 签名私钥。建议用环境变量 `OIDC_PRIVATE_KEY_B64` 固定私钥。

本地执行：

```bash
pip install cryptography
python scripts/generate_private_key_b64.py
```

把输出的一整行复制到 Render 环境变量：

```env
OIDC_PRIVATE_KEY_B64=这里粘贴脚本输出
OIDC_KEY_ID=render-sso-key-1
```

不要频繁更换这个值。更换后 JWKS 会变化，ChatGPT 侧可能短时间缓存旧 key，导致登录失败。

## ChatGPT Team SSO 配置

在 ChatGPT Team 管理后台的 SSO / Custom OIDC 配置中使用：

```text
Issuer / Discovery URL:
https://your-service.onrender.com/.well-known/openid-configuration

Client ID:
chatgpt-team

Client Secret:
和 OIDC_CLIENT_SECRET 一致

Scopes:
openid email profile
```

如果后台要求手动填写端点：

```text
Authorization endpoint:
https://your-service.onrender.com/authorize

Token endpoint:
https://your-service.onrender.com/token

JWKS endpoint:
https://your-service.onrender.com/jwks.json
```

建议先把 ChatGPT Team 的 SSO 保持为 optional，确认管理员和测试用户都能登录后，再改为强制 SSO。

## 本地运行

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env` 后运行：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

打开：

```text
http://localhost:8000/.well-known/openid-configuration
```

## OIDC Endpoints

部署后可访问：

```text
https://your-service.onrender.com/.well-known/openid-configuration
https://your-service.onrender.com/authorize
https://your-service.onrender.com/token
https://your-service.onrender.com/jwks.json
https://your-service.onrender.com/healthz
```

## 安全边界

- 共享密码泄露后，任何人都可能冒用该域名下的任意邮箱前缀登录。
- 如果 `ALLOW_ANY_PREFIX=true`，服务不会验证邮箱真实归属。
- 最好只在可信小团队中使用，并定期更换 `SHARED_PASSWORD`。
- 至少保留一个已登录的管理员会话，避免 SSO 配错后无法进入后台。
