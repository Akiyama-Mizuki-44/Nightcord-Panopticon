"""
宝塔多面板聚合 Dashboard 后端
用法：
    pip install -r requirements.txt
    cp config.example.yaml config.yaml   # 填入各面板地址与 API 密钥
    python app.py
然后浏览器打开 http://127.0.0.1:1810
"""
import hmac
import os
import threading
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify, request, send_from_directory, Response
from werkzeug.security import check_password_hash

from bt_client import BTClient
from nightcord_status_client import NightcordStatusClient
from notifier import Notifier, evaluate_alerts
from auth import BruteForceGuard, get_client_ip
from config_editor import redact_for_display, merge_submitted
import metrics_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"))
metrics_store.init_db()

_brute_force_guard = BruteForceGuard()  # 默认 5 次失败锁 15 分钟，实际阈值以 config.yaml 为准（见 enforce_auth）

# 简单内存缓存，避免每次刷新都重新请求所有面板
_cache = {"data": None, "ts": 0}
CACHE_TTL = 10  # 秒

ALERT_CHECK_INTERVAL = 60  # 秒，后台告警巡检间隔（与前端是否打开无关）
METRICS_PRUNE_INTERVAL = 3600  # 秒，自建监控 agent 历史数据的清理巡检间隔
METRICS_REPORT_PATH = "/api/metrics/report"
QINGYUAN_VERSION = "8.16-beta"  # 青源（自建系统指标监控架构）版本号，见 QINGYUAN.zh-CN.md


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            f"未找到配置文件 {CONFIG_PATH}，请先复制 config.example.yaml 为 config.yaml 并填写面板信息"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_panels():
    return load_config().get("panels", [])


@app.before_request
def enforce_auth():
    """
    Dashboard 现在按设计要长期暴露公网，所以给所有路由加一层 HTTP Basic Auth，
    并按来源 IP 做暴力破解锁定。dashboard_auth.enabled 为 false（默认）时不做任何限制，
    适合只在 WireGuard 内网访问、不打算公网暴露的部署方式。
    """
    if request.path == METRICS_REPORT_PATH:
        return  # agent 机器对机器上报，鉴权走独立的共享密钥（见 api_metrics_report），不走 Basic Auth
    try:
        cfg = load_config()
    except RuntimeError:
        return  # 还没配置 config.yaml，让后续逻辑走正常的报错路径
    auth_cfg = cfg.get("dashboard_auth", {})
    if not auth_cfg.get("enabled"):
        return

    _brute_force_guard.max_attempts = auth_cfg.get("max_attempts", 5)
    _brute_force_guard.lockout_seconds = auth_cfg.get("lockout_seconds", 900)

    ip = get_client_ip(request)
    locked, retry_after = _brute_force_guard.is_locked(ip)
    if locked:
        resp = Response(f"失败次数过多，请 {retry_after} 秒后再试。", status=429)
        resp.headers["Retry-After"] = str(retry_after)
        return resp

    auth = request.authorization
    expected_user = auth_cfg.get("username", "")
    expected_hash = auth_cfg.get("password_hash", "")
    ok = bool(
        auth
        and expected_hash
        and auth.username == expected_user
        and check_password_hash(expected_hash, auth.password)
    )
    if not ok:
        if auth is not None:
            # 只有真的带了(错误的)用户名密码才算一次失败尝试；
            # 浏览器第一次弹登录框前的匿名请求不计数，避免正常访问被误伤。
            _brute_force_guard.record_failure(ip)
        return Response(
            "需要登录才能访问 Nightcord Panopticon。",
            401,
            {"WWW-Authenticate": 'Basic realm="Nightcord Panopticon"'},
        )
    _brute_force_guard.record_success(ip)


def fetch_all():
    panels = load_panels()
    results = []
    with ThreadPoolExecutor(max_workers=max(1, len(panels))) as pool:
        futures = {
            pool.submit(
                BTClient(p["name"], p["url"], p["api_key"], p.get("verify_ssl", False)).collect_all
            ): p
            for p in panels
        }
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                p = futures[fut]
                results.append({"name": p.get("name"), "url": p.get("url"), "online": False, "error": str(e)})
    # 保持配置顺序
    order = {p["name"]: i for i, p in enumerate(panels)}
    results.sort(key=lambda r: order.get(r.get("name"), 999))

    ns_cfg = load_config().get("nightcord_status", {})
    if ns_cfg.get("enabled"):
        try:
            ns_result = NightcordStatusClient(
                ns_cfg.get("name", "Nightcord-Status"),
                ns_cfg["url"],
                ns_cfg.get("verify_ssl", False),
            ).collect_all()
        except Exception as e:
            ns_result = {"name": ns_cfg.get("name", "Nightcord-Status"), "url": ns_cfg.get("url"),
                         "online": False, "error": str(e), "kind": "nightcord-status", "targets": []}
        results.append(ns_result)

    return results


@app.route("/api/status")
def api_status():
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < CACHE_TTL:
        return jsonify({"panels": _cache["data"], "cached": True, "ts": _cache["ts"]})
    try:
        data = fetch_all()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    _cache["data"] = data
    _cache["ts"] = now
    return jsonify({"panels": data, "cached": False, "ts": now})


@app.route(METRICS_REPORT_PATH, methods=["POST"])
def api_metrics_report():
    """自建监控 agent（agent/metrics_agent.py）的上报入口，机器对机器调用。
    鉴权用共享密钥而不是 dashboard_auth 的 Basic Auth（见 enforce_auth 里的例外）。
    """
    cfg = load_config().get("metrics_agent", {})
    if not cfg.get("enabled"):
        return jsonify({"error": "metrics_agent 未启用"}), 404

    expected = cfg.get("shared_secret", "")
    got = request.headers.get("X-Metrics-Secret", "")
    if not expected or not hmac.compare_digest(expected, got):
        return jsonify({"error": "共享密钥不匹配"}), 401

    body = request.get_json(silent=True) or {}
    panel = body.get("panel")
    try:
        ts = int(body["ts"])
        cpu = float(body["cpu"])
        mem = float(body["mem"])
        disk = float(body["disk"])
        net_in_kbps = float(body["net_in_kbps"])
        net_out_kbps = float(body["net_out_kbps"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "缺少字段或字段类型不对，需要 panel/ts/cpu/mem/disk/net_in_kbps/net_out_kbps"}), 400
    if not panel:
        return jsonify({"error": "缺少 panel"}), 400

    metrics_store.insert_sample(panel, ts, cpu, mem, disk, net_in_kbps, net_out_kbps)
    return jsonify({"ok": True})


@app.route("/api/metrics/version")
def api_metrics_version():
    return jsonify({"name": "青源", "version": QINGYUAN_VERSION})


@app.route("/api/metrics/history")
def api_metrics_history():
    """给前端历史趋势图用的读接口，跟其它面板数据一样走 dashboard_auth。"""
    panel = request.args.get("panel", "")
    if not panel:
        return jsonify({"error": "缺少 panel 参数"}), 400
    hours = request.args.get("hours", 24, type=float) or 24
    since_ts = int(time.time() - hours * 3600)
    return jsonify({"panel": panel, "samples": metrics_store.query_history(panel, since_ts)})


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/setup")
def setup_page():
    # /setup 和 /settings 是同一个页面：面板列表为空时它就是"首次配置向导"，
    # 已经配置过之后就是普通的设置页，逻辑都在前端 JS 里根据 GET /api/config 的返回判断。
    return send_from_directory(app.static_folder, "settings.html")


@app.route("/settings")
def settings_page():
    return send_from_directory(app.static_folder, "settings.html")


@app.route("/api/config", methods=["GET"])
def api_get_config():
    try:
        cfg = load_config()
    except RuntimeError:
        cfg = {}
    return jsonify(redact_for_display(cfg))


@app.route("/api/config", methods=["POST"])
def api_save_config():
    try:
        old_cfg = load_config()
    except RuntimeError:
        old_cfg = {}
    submitted = request.get_json(silent=True) or {}
    merged, errors = merge_submitted(old_cfg, submitted)
    if errors:
        return jsonify({"errors": errors}), 400
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    _cache["data"] = None  # 配置变了，强制下次 /api/status 重新拉取而不是用旧缓存
    return jsonify({"ok": True})


def background_alert_loop():
    """后台巡检线程：定期拉取所有面板数据、判断阈值、发送告警，并顺带刷新缓存。"""
    while True:
        try:
            cfg = load_config()
            data = fetch_all()
            _cache["data"] = data
            _cache["ts"] = time.time()

            notif_cfg = cfg.get("notifications", {})
            thresholds = notif_cfg.get("thresholds", {})
            notifier = Notifier(notif_cfg)
            for panel_result in data:
                for key, title, content in evaluate_alerts(panel_result, thresholds):
                    notifier.notify(key, title, content)
        except Exception as e:
            print(f"[background_alert_loop] 出错: {e}")
        time.sleep(ALERT_CHECK_INTERVAL)


def background_metrics_prune_loop():
    """后台线程：定期清理超过保留窗口的自建监控历史数据，防止 metrics.db 无限增长。"""
    while True:
        try:
            cfg = load_config().get("metrics_agent", {})
            retention_days = cfg.get("retention_days", 30)
            cutoff_ts = int(time.time() - retention_days * 86400)
            metrics_store.prune_older_than(cutoff_ts)
        except Exception as e:
            print(f"[background_metrics_prune_loop] 出错: {e}")
        time.sleep(METRICS_PRUNE_INTERVAL)


if __name__ == "__main__":
    t = threading.Thread(target=background_alert_loop, daemon=True)
    t.start()
    t2 = threading.Thread(target=background_metrics_prune_loop, daemon=True)
    t2.start()
    debug = os.environ.get("FLASK_DEBUG") == "1"
    port = int(os.environ.get("PORT", 1810))
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
