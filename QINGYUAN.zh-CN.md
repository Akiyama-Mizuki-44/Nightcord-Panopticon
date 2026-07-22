# 青源（Qingyuan）

自建系统指标监控架构的名字，覆盖 `agent/metrics_agent.py`（面板服务器上的采集/上报端）
和 Panopticon 侧的 `metrics_store.py` + `app.py` 里的 `/api/metrics/*` 接口这一整条链路。
跟 BT 面板 API 那条链路（`bt_client.py`）并列、互不影响——业务数据继续问 BT，系统级指标走青源。

- **当前版本**：`8.16-beta`
- **内部开发代号**：`yukikaze`（仅内部记录用，不出现在面向用户的文档/界面里）
- **状态**：beta——接口、存储 schema、前端历史趋势图都已经过本机端到端验证（鉴权、字段校验、历史读写、
  清理、图表渲染都测过），也已经在真实生产环境的多台服务器上跑通（同名机器自动合并卡片、跨 WireGuard
  上报、ufw 放行等都验证过），仍标 beta 是因为还没经过长时间稳定性观察。

## 组成

| 文件 | 作用 |
|---|---|
| `agent/metrics_agent.py` | 跑在各面板服务器，采集 CPU/内存/磁盘/网络，定时 push |
| `agent/agent_config.example.yaml` | agent 的配置模板（手动部署时用，见 `agent/README.zh-CN.md`） |
| `agent/nightcord-metrics-agent.service` | agent 的 systemd 部署示例 |
| `metrics_store.py` | Panopticon 侧的 SQLite 存储层（`metrics.db`） |
| `agent_deploy.py` | 一键部署：SSH 到目标机器自动装 agent、写配置、注册并启动 systemd |
| `agent_hosts.py` | 一键部署时"记住密码"选项的加密存储层（密钥文件 `agent_hosts.key`，不进仓库） |
| `app.py` 里的 `POST /api/metrics/report` | agent 上报入口，共享密钥鉴权 |
| `app.py` 里的 `GET /api/metrics/history` | 历史数据读接口，走 dashboard_auth |
| `app.py` 里的 `GET /api/metrics/version` | 返回当前青源版本号 |
| `app.py` 里的 `/api/agent/deploy`、`/api/agent/hosts*` | 一键部署相关接口，走 dashboard_auth |

## 部署方式

两种都行，效果一样：

1. **一键部署（推荐）**：`/settings` 页面「一键部署 agent」区块，填服务器 IP / 端口 / SSH 账号密码，
   点一下自动装完（对应 `agent_deploy.py`）。`metrics_agent.enabled` 和 `shared_secret` 会在第一次
   一键部署时自动补全，不用再手动改 `config.yaml`。
2. **手动部署**：见 `agent/README.zh-CN.md`，自己 rsync 代码、装依赖、写 `agent_config.yaml`、配 systemd。

一键部署目前假设目标机器上有 `python3`（含 `venv` 模块）和 `systemd`；账号不是 `root` 的话会自动走
`sudo -S`（要求 sudo 密码跟 SSH 登录密码一致）。主机指纹用的是"首次连接自动信任"（TOFU），部署日志里
会打印出来，多疑的话可以自己去目标机核对。

## agent 怎么连回 Panopticon：要不要 WireGuard

agent 上报走的是 `config.yaml` 里 `metrics_agent.report_url`（没填的话会退而求其次猜一个，见下面
「一键部署踩坑」）。这个地址该填什么，取决于 agent 跑在哪：

- **agent 跟 Panopticon 是同一台机器**（比如宝塔面板服务器本身也跑着 Panopticon）：直接填
  `http://127.0.0.1:1810` 就行，不需要 WireGuard，本机回环访问不存在跨网络的问题。
- **agent 跑在别的机器上**：这时候才需要 Panopticon 和这台机器之间有一条互通的内网路径——
  WireGuard 是推荐做法（见 [README.zh-CN.md](README.zh-CN.md#推荐架构panopticon-长期公网可见宝塔面板后台完全收进-wireguard)
  的整体架构说明），`report_url` 填 Panopticon 在 WireGuard 网段里的地址（例如 `http://10.10.0.1:1810`）。
  不建议图省事直接把 1810 端口暴露公网——这个接口只有共享密钥鉴权，没有来源限制，公网直连意味着
  任何拿到密钥的人都能往里灌假数据，攻击面跟"必要性"完全不成比例。

判断标准就一句话：**只要 Panopticon 和 agent 不在同一台机器上，就需要某种内网互通方式，WireGuard 是
目前唯一测过、文档化的方案**；只有 agent 装在 Panopticon 自己身上这一种情况可以完全跳过组网。

### 一键部署踩坑：`report_url` 猜错

`metrics_agent.report_url` 不填的话，一键部署会拿"你点安装按钮那一刻浏览器访问设置页用的地址"顶上——
如果你是通过 SSH 隧道（`tunnel.sh`）访问 `127.0.0.1:1810`，但部署目标是别的机器，这个猜测就是错的：
agent 会拿到一个只在它自己身上才有意义的地址，上报永远静默失败，dashboard 上也不会出现这张卡片，
而且不会有任何报错提示你发生了什么。**只要会给"本机以外"的机器装 agent，就强烈建议先手动把
`metrics_agent.report_url` 填好**，一键部署检测到这种情况也会在安装日志里弹警告，但填了就不用等它提醒。

## 版本历史

- `8.16-beta`（代号 `yukikaze`）——首个可用版本：agent 采集/上报 + SQLite 历史存储 + 接收端接口 +
  前端历史趋势图（CPU/内存/磁盘三线 SVG 图表，支持 1h/6h/24h/7d 范围切换；无上报数据的面板不显示该区块）。
