# minimal-oidc-sharedpass for Render

这是一个最小化的 OIDC Provider，可用于 ChatGPT Business / Custom OIDC 测试。

登录页会要求用户输入：

- 邮箱前缀，例如 `alice`
- 共享密码

验证通过后，它会向 OIDC 客户端签发 `alice@EMAIL_DOMAIN` 的 `id_token`。

> 重要：这是轻量共享密码方案，适合小范围测试或临时内部使用，不等同于企业级 IdP。生产环境建议改成真实邮箱验证、企业目录、MFA 或成熟 IdP。

## Render 部署方式

### 方式 A：Blueprint 部署

1. 把本项目推送到 GitHub。
2. 在 Render 里选择 **New > Blueprint**。
3. 选择这个仓库，Render 会读取 `render.yaml`。
4. 填写所有 `sync: false` 的环境变量。
5. 部署完成后，把 `ISSUER` 改成 Render 分配的公网地址，例如：

```text
https://your-service.onrender.com
```

### 方式 B：Web Service 部署

1. 在 Render 里选择 **New > Web Service**。
2. 连接 GitHub 仓库。
3. Runtime 选择 **Docker**。
4. Health Check Path 填：

```text
/healthz
```

5. 添加下面的环境变量。

## 必填环境变量

```env
ISSUER=https://your-service.onrender.com
OIDC_CLIENT_ID=chatgpt-business
OIDC_CLIENT_SECRET=replace-with-a-long-random-secret
OIDC_REDIRECT_URI=paste-the-exact-chatgpt-oidc-redirect-uri-here
EMAIL_DOMAIN=example.com
SHARED_PASSWORD=replace-with-your-shared-login-password
ALLOW_ANY_PREFIX=true
ALLOWED_PREFIXES=
TOKEN_TTL_SECONDS=300
OIDC_PRIVATE_KEY_B64=paste-generated-value-here
OIDC_KEY_ID=render-sso-key-1
```

说明：

- `ISSUER` 必须是你的 Render 服务公网地址，不能带结尾 `/`。
- `OIDC_REDIRECT_URI` 必须与 ChatGPT Business / Custom OIDC 页面显示的 callback/redirect URI 完全一致。
- `EMAIL_DOMAIN` 应该是你在 ChatGPT Business 里验证过的域名。
- `ALLOW_ANY_PREFIX=true` 表示任意合法前缀都能用共享密码登录。
- 如果想限制用户，设为 `ALLOW_ANY_PREFIX=false`，并配置 `ALLOWED_PREFIXES=alice,bob,charlie`。

## 生成稳定签名私钥

Render 的文件系统在无持久磁盘时不适合保存 OIDC 签名密钥。建议使用环境变量 `OIDC_PRIVATE_KEY_B64` 固定私钥。

本地执行：

```bash
pip install cryptography
python scripts/generate_private_key_b64.py
```

把输出的一整行复制到 Render 环境变量：

```env
OIDC_PRIVATE_KEY_B64=这里粘贴输出
OIDC_KEY_ID=render-sso-key-1
```

不要频繁更换这个值。更换后 JWKS 会变化，ChatGPT 侧可能短时间内仍缓存旧 key，导致登录失败。

## OIDC Endpoints

部署后可访问：

```text
https://your-service.onrender.com/.well-known/openid-configuration
https://your-service.onrender.com/authorize
https://your-service.onrender.com/token
https://your-service.onrender.com/jwks.json
https://your-service.onrender.com/healthz
```

## ChatGPT Business / Custom OIDC 配置

在 ChatGPT Business 的 SSO / Custom OIDC 配置中使用：

```text
Issuer / Discovery URL:
https://your-service.onrender.com/.well-known/openid-configuration

Client ID:
与 OIDC_CLIENT_ID 一致

Client Secret:
与 OIDC_CLIENT_SECRET 一致

Scopes:
openid email profile
```

如果后台要求手动填端点：

```text
Authorization endpoint: https://your-service.onrender.com/authorize
Token endpoint: https://your-service.onrender.com/token
JWKS endpoint: https://your-service.onrender.com/jwks.json
```

## 本地测试

```bash
cp .env.example .env
# 编辑 .env 后：
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
set -a; . .env; set +a
uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

打开：

```text
http://localhost:8000/.well-known/openid-configuration
```

## Docker 本地运行

```bash
cp docker-compose.yml docker-compose.local.yml
# 编辑 docker-compose.local.yml 后：
docker compose -f docker-compose.local.yml up -d --build
```

## 安全提醒

- 共享密码泄露后，任何人都能冒用该域名下任意前缀登录。
- 测试时建议先保持 ChatGPT SSO optional，确认能登录后再强制启用。
- 至少保留一个已登录管理员会话，避免 SSO 配错后无法进入后台。
