"""
宝塔多面板聚合 Dashboard 后端
用法：
    pip install -r requirements.txt
    cp config.example.yaml config.yaml   # 填入各面板地址与 API 密钥
    python app.py
然后浏览器打开 http://127.0.0.1:5000
"""
import os
import threading
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify, send_from_directory

from bt_client import BTClient
from notifier import Notifier, evaluate_alerts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"))

# 简单内存缓存，避免每次刷新都重新请求所有面板
_cache = {"data": None, "ts": 0}
CACHE_TTL = 10  # 秒

ALERT_CHECK_INTERVAL = 60  # 秒，后台告警巡检间隔（与前端是否打开无关）


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            f"未找到配置文件 {CONFIG_PATH}，请先复制 config.example.yaml 为 config.yaml 并填写面板信息"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_panels():
    return load_config().get("panels", [])


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


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


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


if __name__ == "__main__":
    t = threading.Thread(target=background_alert_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
