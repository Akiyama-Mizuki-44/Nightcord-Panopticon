[English](README.md) | **简体中文**

# nightcord-panopticon

> Nightcord 系列 · 宝塔多面板聚合 Dashboard

自建的轻量级全局监控面板，聚合多台宝塔（BT Panel）服务器的状态、网站、数据库信息，
用后端代理调用各面板 API（避免浏览器直连宝塔 API 的 CORS / IP 白名单限制）。

## 架构

```
浏览器 <-- HTTP --> Flask 后端 (app.py) <-- 签名请求 --> 各台宝塔面板 API
```

- `bt_client.py`：封装宝塔 API 的签名算法（`md5(request_time + md5(api_sk))`）与常用接口调用。
- `app.py`：Flask 服务，读取 `config.yaml` 中配置的多台面板，并发拉取数据，暴露 `/api/status`，并托管前端页面。
- `static/index.html`：单页 Dashboard，每 15 秒轮询一次 `/api/status`，展示 CPU/内存/磁盘、站点列表、数据库数量等。

## 已接入的接口

| 功能 | 接口 |
|---|---|
| CPU / 内存 / 系统信息 | `GET/POST /system?action=GetSystemTotal` |
| 网络流量 / 负载 | `GET/POST /system?action=GetNetWork` |
| 磁盘使用情况 | `GET/POST /system?action=GetDiskInfo` |
| 网站列表 | `GET/POST /data?action=getData&table=sites` |
| 数据库列表 | `GET/POST /data?action=getData&table=databases` |

宝塔官方 API 文档目前仍"未写完"（官方原话），字段可能因面板版本略有差异；
每张卡片下方都有「查看原始数据」的折叠区，可直接看到该接口的完整 JSON 返回，
方便你按需扩展（例如加 SSL 证书到期、防火墙拦截统计、计划任务状态等）。

通用配置指南（不绑定具体服务器）见 [SETUP.zh-CN.md](SETUP.zh-CN.md)；一份具体案例（含宝塔 API 设置、WireGuard 组网、systemd、Nginx+HTTPS）见 [DEPLOY.zh-CN.md](DEPLOY.zh-CN.md)。

## 使用步骤

### 1. 在每台宝塔面板开启 API 并获取密钥

面板后台 → 设置 → API 接口 → 打开接口状态，复制"接口密钥"。
同一页面的 **IP 白名单** 中，把运行本 Dashboard 后端的服务器 IP 加进去
（如果后端和面板在同一台机器上，还要加 `127.0.0.1`）。

### 2. 安装依赖

```bash
cd nightcord-panopticon
pip install -r requirements.txt
```

### 3. 配置面板列表

两种方式任选一种：

**方式 A：可视化设置页（推荐）**——直接启动服务（见下一步），浏览器打开
`http://127.0.0.1:1810/setup`，第一次访问时它就是配置向导：填面板名称/地址/API 密钥、
登录密码、告警阈值、飞书/邮箱推送，保存后立即生效。之后这个页面常驻在 `/settings`，
随时可以回来改，改的时候密钥类字段留空 = 不修改。

**方式 B：手动编辑 YAML**
```bash
cp config.example.yaml config.yaml
```
编辑 `config.yaml`，为每台服务器填入 `name` / `url`（含端口，如 `http://1.2.3.4:18101`）/ `api_key`，
其余字段（`dashboard_auth` 的密码要用 `gen_password_hash.py` 生成哈希）见文件里的注释。

### 4. 启动

```bash
python app.py
```

浏览器打开 `http://127.0.0.1:1810` 即可看到聚合后的全局面板；如果还没配置过，会自动引导你去
`/setup`。

## 安全提示

- `config.yaml` 里是明文密钥，注意权限控制，不要提交到公开代码仓库（本仓库已通过 `.gitignore` 排除）。
- 生产环境不要用 Flask 自带的开发服务器，可用 `gunicorn app:app` 或反向代理。

### 推荐架构：Panopticon 长期公网可见，宝塔面板后台完全收进 WireGuard

日常小操作（看状态、看告警）直接公网访问 Panopticon，不用连 VPN；真要动数据库这类敏感操作，
必须登录宝塔面板后台，而**面板后台完全不对公网开放**，只能从 WireGuard 内网连。Panopticon 和
各台面板之间的 API 通信，也全程走 WireGuard 隧道。

```
[你，随时随地] --公网 HTTPS--> [Panopticon Dashboard，一直暴露公网，带登录+防爆破]
                                          │
                                    WireGuard 隧道（Dashboard 是 WG 里的一个节点）
                                          │
                              [宝塔面板后台，完全不对公网开放，只信 WireGuard，端口 18101]
```

1. **面板改端口 + 完全关闭公网访问**：面板后台 → 设置 → 面板端口改成 `18101`。然后在每台宝塔服务器上用 `ufw` 彻底拒绝公网访问该端口，只放行 WireGuard 网段：
   ```bash
   ufw deny 18101/tcp
   ufw allow in on wg0 to any port 18101 proto tcp
   ```
   网站端口（如果有对外服务）不受影响，正常放行。面板的 **IP 白名单** 也可以顺手填成 WireGuard 内网 IP 作为双保险。

2. **组网**：Panopticon 所在的服务器，作为 WireGuard 的一个节点加入你的私有网段（如 `10.10.0.0/24`），这样它虽然对公网开放 HTTP(S) 服务，但访问面板 API 时走的是它自己的 WireGuard 出口，不经过公网。

3. **Dashboard 侧配置**：`config.yaml` 里每台面板的 `url` 填该面板的 WireGuard 内网地址 + 18101 端口，例如 `http://10.10.0.2:18101`（把 `10.10.0.x` 换成你自己的真实内网 IP，不用告诉我）。

4. **给 Panopticon 本身加登录门槛（关键）**：既然它要长期公网暴露，而且手里握着能连进你 WireGuard 内网、调用所有面板 API 的凭证，必须有登录验证，否则等于把内网入口的钥匙放在门口。本项目内置了这个能力，见下一节。

### Panopticon 自身的登录验证 + 防暴力破解

`app.py` 内置了一层 HTTP Basic Auth，外加按来源 IP 的失败次数锁定，默认关闭（`dashboard_auth.enabled: false`），
**只要打算公网暴露就必须打开**：

```bash
python gen_password_hash.py "你的密码"   # 生成哈希，不要在 config.yaml 里写明文密码
```

把输出粘到 `config.yaml`：

```yaml
dashboard_auth:
  enabled: true
  username: "mizuki"
  password_hash: "上一步生成的哈希"
  max_attempts: 5        # 同一 IP 连续失败这么多次后锁定
  lockout_seconds: 900   # 锁定时长（秒），默认 15 分钟
```

行为：没有认证头或用户名密码不对 → 401；同一 IP 累计失败次数达到 `max_attempts` → 后续请求（哪怕密码是对的）
都会被 429 拒绝，直到锁定期过去。锁定状态存在内存里，重启 Dashboard 会重置。

如果你只打算在 WireGuard 内网访问 Panopticon（不公网暴露），`enabled` 保持 `false` 即可，省掉每次输密码的麻烦。

## 可扩展方向

- SSL 证书到期提醒：调用 `/site?action=GetSSL&siteName=xxx`（需按官方 PDF 核对最新参数名）。
- 安全告警：面板日志 / 防火墙拦截接口。
- 历史趋势图：把 `/api/status` 的采样结果写入 SQLite，用 Chart.js 画曲线。
- 钉钉 / 企业微信 / Server 酱告警推送，触发条件如 CPU>90%、磁盘>85%、面板离线等。

## 备注

如果不想自己维护聚合逻辑，宝塔官方也提供付费的"堡塔多机管理"产品，
可实现类似的多机统一管理，见 https://www.bt.cn/new/product_pc.html 。
本项目适合想要完全自控、免费、可按需定制字段的场景。

## License

MIT © Akiyama Mizuki · Nightcord Series
