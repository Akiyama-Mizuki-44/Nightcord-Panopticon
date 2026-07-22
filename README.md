**English** | [简体中文](README.zh-CN.md)

# nightcord-panopticon

> Nightcord Series · Multi-panel aggregated dashboard for BT Panel (宝塔)

A lightweight, self-hosted global monitoring dashboard that aggregates status, sites, and
database info across multiple BT Panel (宝塔面板) servers. A backend proxy signs and calls
each panel's API on your behalf, avoiding the CORS / IP-whitelist issues you'd hit calling
the BT Panel API directly from the browser.

Beyond BT Panel, it also runs **青源 (Qingyuan)**: an optional self-hosted metrics agent you
one-click-deploy (over SSH) to any Linux box — panel or not — for real per-mount disk usage,
CPU/memory, network throughput, and threshold-based alerts (Feishu card / email). A separate
lightweight reporter can feed ESXi hosts into the same pipeline via vSphere API without
installing anything on the hypervisor itself. See [QINGYUAN.zh-CN.md](QINGYUAN.zh-CN.md) and
[ESXI_QINGYUAN_MONITORING.zh-CN.md](ESXI_QINGYUAN_MONITORING.zh-CN.md) (Chinese only, commands
are copy-pasteable) for the full picture.

## Architecture

```
Browser <-- HTTP --> Flask backend (app.py) <-- signed requests --> each BT Panel API
                                 │
                                 ├─ SQLite (metrics_store.py) <-- push reports -- Qingyuan agents
                                 │                                               (one per host)
                                 └─ Feishu card / email alerts (notifier.py)
```

- `bt_client.py`: implements BT Panel's request-signing scheme (`md5(request_time + md5(api_sk))`) and wraps the common endpoints.
- `app.py`: Flask service that reads the panel list from `config.yaml`, fetches all panels concurrently, exposes `/api/status`, merges in Qingyuan data, and serves the frontend.
- `static/index.html`: single-page dashboard, polls `/api/status` every 15s, and renders CPU/memory/disk (per mount point), site list, database count, IP addresses, and history trend charts.
- `agent/metrics_agent.py` + `agent_deploy.py`: the Qingyuan agent itself, and the one-click SSH installer driven from the settings page.
- `esxi_reporter/esxi_metrics_reporter.py`: standalone script (runs elsewhere, e.g. on a box that already talks to vSphere) that feeds ESXi host metrics into the same `/api/metrics/report` endpoint.
- `notifier.py`: threshold evaluation + edge-triggered Feishu/email alerts (fires once when a problem starts, not on every check cycle).
- `webauthn_manager.py` + `static/login.html`: session-based login with optional Passkey (WebAuthn) support alongside the password.

## Endpoints in use

| Purpose | Endpoint |
|---|---|
| CPU / memory / system info | `GET/POST /system?action=GetSystemTotal` |
| Network throughput / load | `GET/POST /system?action=GetNetWork` |
| Disk usage | `GET/POST /system?action=GetDiskInfo` |
| Site list | `GET/POST /data?action=getData&table=sites` |
| Database list | `GET/POST /data?action=getData&table=databases` |

BT Panel's official API docs are still marked "incomplete" by BT themselves, so field names
can vary slightly by panel version. Every card has a collapsible "raw data" section showing
the full JSON response, so you can extend it as needed (SSL expiry, firewall block counts,
cron job status, etc.).

General setup guide (not tied to a specific server) at [SETUP.md](SETUP.md); a worked example (BT Panel API setup, WireGuard mesh, systemd, Nginx+HTTPS) at [DEPLOY.zh-CN.md](DEPLOY.zh-CN.md) (Chinese only, commands are still copy-pasteable).

## Usage

### 1. Enable the API on each BT Panel and grab a key

Panel dashboard → Settings → API interface → turn it on, copy the "接口密钥" (API secret key).
On the same page, add the IP of the server running this dashboard's backend to the
**IP whitelist** (add `127.0.0.1` too if the backend runs on the same machine as a panel).

### 2. Install dependencies

```bash
cd nightcord-panopticon
pip install -r requirements.txt
```

### 3. Configure your panel list

Two options:

**Option A: the visual settings page (recommended)** — just start the service (next step) and open
`http://127.0.0.1:1810/setup` in your browser. On first run it acts as a setup wizard: panel
name/URL/API key, login password, alert thresholds, Feishu/email notifications — all saved
immediately. It stays available afterwards at `/settings`; when editing, leave secret fields
blank to keep them unchanged.

**Option B: edit the YAML by hand**
```bash
cp config.example.yaml config.yaml
```
Edit `config.yaml` and fill in `name` / `url` (with port, e.g. `http://1.2.3.4:18101`) / `api_key`
for every server; see the comments in the file for the rest (`dashboard_auth`'s password needs a
hash from `gen_password_hash.py`).

### 4. Run

```bash
python app.py
```

Open `http://127.0.0.1:1810` in your browser to see the aggregated dashboard.

## Security notes

- `config.yaml` holds plaintext secrets — keep it out of public repos (already excluded via `.gitignore`).
- Don't run Flask's dev server in production; use `gunicorn app:app` behind a reverse proxy instead.

### Recommended architecture: Panopticon stays public, BT Panel admin is WireGuard-only

For day-to-day checks (status, alerts) you hit Panopticon directly over the public internet — no
VPN client needed. For anything sensitive (databases, etc.) you log into the actual BT Panel admin
UI, and **that admin UI is never reachable from the public internet**, only from inside WireGuard.
API traffic between Panopticon and every panel also stays inside the WireGuard tunnel.

```
[you, anywhere] --public HTTPS--> [Panopticon dashboard, always public, login + brute-force lockout]
                                          │
                                    WireGuard tunnel (the dashboard is a peer on this mesh)
                                          │
                              [BT Panel admin UI, no public access at all, WireGuard-only, port 18101]
```

1. **Move the panel port and close it to the public entirely**: Panel dashboard → Settings → Panel Port → `18101`. Then on each BT Panel server, deny that port publicly and only allow it from the WireGuard subnet with `ufw`:
   ```bash
   ufw deny 18101/tcp
   ufw allow in on wg0 to any port 18101 proto tcp
   ```
   Site ports (if the box also serves public sites) are unaffected. You can also set the panel's **IP whitelist** to the WireGuard internal IP as a second layer.

2. **Mesh**: the server running Panopticon joins the same WireGuard subnet (e.g. `10.10.0.0/24`) as every panel. It still serves HTTP(S) to the public internet, but all outbound calls to panel APIs go out over its own WireGuard interface, never touching the public internet.

3. **Dashboard config**: set each panel's `url` in `config.yaml` to its WireGuard internal address on port 18101, e.g. `http://10.10.0.2:18101` (swap in your real internal IPs — no need to share them with me).

4. **Lock down Panopticon itself (important)**: since it's public and holds credentials that reach into your WireGuard network and call every panel's API, it needs a login. This is built in — see the next section.

### Login + brute-force protection on Panopticon itself

`app.py` gates every route behind a session-based login (password, with optional Passkey /
WebAuthn as a second way in — fingerprint, Face ID, security key), plus per-IP failure lockout.
Disabled by default (`dashboard_auth.enabled: false`). **Turn it on before exposing this
publicly**:

```bash
python gen_password_hash.py "your password"   # generates a hash — never put a plaintext password in config.yaml
```

Paste the output into `config.yaml`:

```yaml
dashboard_auth:
  enabled: true
  username: "mizuki"
  password_hash: "the hash from the previous step"
  max_attempts: 5        # lock an IP out after this many consecutive failures
  lockout_seconds: 900   # lockout duration in seconds, default 15 minutes
```

Behavior: no/invalid credentials → redirected to `/login` (or `401` for API calls). Once one IP
accumulates `max_attempts` failures, every subsequent request from it — even with the correct
password — gets a `429` until the lockout window passes. Lockout state lives in memory and resets
when the dashboard restarts. Passkeys are registered from `/settings` after your first password
login, and require HTTPS (`localhost`/`127.0.0.1` excluded) since WebAuthn won't work over plain
HTTP otherwise.

If you only ever access Panopticon from inside WireGuard and don't plan to expose it publicly, leave `enabled: false` and skip the login prompt entirely.

## Ideas for extension

- SSL expiry alerts: call `/site?action=GetSSL&siteName=xxx` (verify the latest parameter names against BT's official PDF).
- Security alerts: panel logs / firewall block stats.
- Windows agent support: `agent/metrics_agent.py` already uses cross-platform `psutil`, but the one-click installer (`agent_deploy.py`) is SSH + systemd only, and the disk collector assumes POSIX mount points.
- Network throughput for the ESXi reporter: currently reports `0`, since it needs `PerformanceManager` counters instead of the simpler `quickStats` fields used for CPU/memory (see the "known limitations" section in [ESXI_QINGYUAN_MONITORING.zh-CN.md](ESXI_QINGYUAN_MONITORING.zh-CN.md)).

## Note

If you'd rather not maintain the aggregation logic yourself, BT Panel also offers a paid
"堡塔多机管理" (multi-server management) product with similar functionality — see
https://www.bt.cn/new/product_pc.html. This project is for anyone who wants full control,
zero cost, and custom fields.

## License

MIT © Akiyama Mizuki · Nightcord Series
