# 青源（Qingyuan）

自建系统指标监控架构的名字，覆盖 `agent/metrics_agent.py`（面板服务器上的采集/上报端）
和 Panopticon 侧的 `metrics_store.py` + `app.py` 里的 `/api/metrics/*` 接口这一整条链路。
跟 BT 面板 API 那条链路（`bt_client.py`）并列、互不影响——业务数据继续问 BT，系统级指标走青源。

- **当前版本**：`8.16-beta`
- **内部开发代号**：`yukikaze`（仅内部记录用，不出现在面向用户的文档/界面里）
- **状态**：beta——接口和存储 schema 已经过本机端到端验证（鉴权、字段校验、历史读写、清理都测过），
  但还没有在两台面板服务器上实际跑过，也还没接前端历史趋势图。

## 组成

| 文件 | 作用 |
|---|---|
| `agent/metrics_agent.py` | 跑在各面板服务器，采集 CPU/内存/磁盘/网络，定时 push |
| `agent/agent_config.example.yaml` | agent 的配置模板 |
| `agent/nightcord-metrics-agent.service` | agent 的 systemd 部署示例 |
| `metrics_store.py` | Panopticon 侧的 SQLite 存储层（`metrics.db`） |
| `app.py` 里的 `POST /api/metrics/report` | agent 上报入口，共享密钥鉴权 |
| `app.py` 里的 `GET /api/metrics/history` | 历史数据读接口，走 dashboard_auth |
| `app.py` 里的 `GET /api/metrics/version` | 返回当前青源版本号 |

## 版本历史

- `8.16-beta`（代号 `yukikaze`）——首个可用版本：agent 采集/上报 + SQLite 历史存储 + 接收端接口。
  前端历史趋势图尚未实现。
