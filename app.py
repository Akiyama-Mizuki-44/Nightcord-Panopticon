"""
宝塔多面板聚合 Dashboard 后端
用法：
    pip install -r requirements.txt
    cp config.example.yaml config.yaml   # 填入各面板地址与 API 密钥
    python app.py
然后浏览器打开 http://127.0.0.1:5000
"""
import os
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify, send_from_directory

from bt_client import BTClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"))

# 简单内存缓存，避免每次刷新都重新请求所有面板
_cache = {"data": None, "ts": 0}
CACHE_TTL = 10  # 秒


def load_panels():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            f"未找到配置文件 {CONFIG_PATH}，请先复制 config.example.yaml 为 config.yaml 并填写面板信息"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("panels", [])


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
