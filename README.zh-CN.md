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

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，为每台服务器填入 `name` / `url`（含端口，如 `http://1.2.3.4:8888`）/ `api_key`。

### 4. 启动

```bash
python app.py
```

浏览器打开 `http://127.0.0.1:5000` 即可看到聚合后的全局面板。

## 安全提示

- `config.yaml` 里是明文密钥，注意权限控制，不要提交到公开代码仓库（本仓库已通过 `.gitignore` 排除）。
- 建议把本 Dashboard 部署在内网或加一层 Nginx Basic Auth / VPN 访问，不要直接暴露公网。
- 生产环境不要用 Flask 自带的开发服务器，可用 `gunicorn app:app` 或反向代理。

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
