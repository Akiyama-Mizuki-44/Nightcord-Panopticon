**English** | [简体中文](README.zh-CN.md)

# nightcord-panopticon

> Nightcord Series · Multi-panel aggregated dashboard for BT Panel (宝塔)

A lightweight, self-hosted global monitoring dashboard that aggregates status, sites, and
database info across multiple BT Panel (宝塔面板) servers. A backend proxy signs and calls
each panel's API on your behalf, avoiding the CORS / IP-whitelist issues you'd hit calling
the BT Panel API directly from the browser.

## Architecture

```
Browser <-- HTTP --> Flask backend (app.py) <-- signed requests --> each BT Panel API
```

- `bt_client.py`: implements BT Panel's request-signing scheme (`md5(request_time + md5(api_sk))`) and wraps the common endpoints.
- `app.py`: Flask service that reads the panel list from `config.yaml`, fetches all panels concurrently, exposes `/api/status`, and serves the frontend.
- `static/index.html`: single-page dashboard, polls `/api/status` every 15s, and renders CPU/memory/disk, site list, database count, etc.

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

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` and fill in `name` / `url` (with port, e.g. `http://1.2.3.4:8888`) / `api_key`
for every server.

### 4. Run

```bash
python app.py
```

Open `http://127.0.0.1:5000` in your browser to see the aggregated dashboard.

## Security notes

- `config.yaml` holds plaintext secrets — keep it out of public repos (already excluded via `.gitignore`).
- Deploy this behind an internal network / VPN, or put Nginx Basic Auth in front of it. Don't expose it directly to the public internet.
- Don't run Flask's dev server in production; use `gunicorn app:app` behind a reverse proxy instead.

## Ideas for extension

- SSL expiry alerts: call `/site?action=GetSSL&siteName=xxx` (verify the latest parameter names against BT's official PDF).
- Security alerts: panel logs / firewall block stats.
- Historical trend charts: persist `/api/status` samples into SQLite and plot with Chart.js.
- Push notifications (DingTalk / WeCom / Server酱) on thresholds like CPU>90%, disk>85%, or a panel going offline.

## Note

If you'd rather not maintain the aggregation logic yourself, BT Panel also offers a paid
"堡塔多机管理" (multi-server management) product with similar functionality — see
https://www.bt.cn/new/product_pc.html. This project is for anyone who wants full control,
zero cost, and custom fields.

## License

MIT © Akiyama Mizuki · Nightcord Series
