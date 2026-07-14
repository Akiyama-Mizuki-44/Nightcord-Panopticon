"""
宝塔多面板聚合 Dashboard 后端
用法：
    pip install -r requirements.txt
    cp config.example.yaml config.yaml   # 填入各面板地址与 API 密钥
    python app.py
然后浏览器打开 http://127.0.0.1:1810
"""
import json
import os
import secrets
import threading
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify, request, send_from_directory, Response, session, redirect, url_for
from werkzeug.security import check_password_hash

import webauthn_manager as wam
from bt_client import BTClient
from notifier import Notifier, evaluate_alerts
from auth import BruteForceGuard, get_client_ip
from config_editor import redact_for_display, merge_submitted

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"))
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

_brute_force_guard = BruteForceGuard()  # 默认 5 次失败锁 15 分钟，实际阈值以 config.yaml 为准（见 enforce_auth）

# 不需要登录就能访问的路由：登录页本身，以及登录相关的两个 API（否则没法登录）
PUBLIC_ENDPOINTS = {"login_page", "login_password", "logout", "webauthn_login_options", "webauthn_login_verify"}

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


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def ensure_session_secret():
    """
    session cookie 签名密钥；持久化在 config.yaml 里，
    这样多进程(gunicorn 多 worker)部署时大家读到的是同一个值，重启也不会互相把对方的 session 弄失效。
    config.yaml 还不存在（第一次运行、还没走完配置向导）时用一个临时的随机值顶着。
    """
    try:
        cfg = load_config()
    except RuntimeError:
        return secrets.token_hex(32)
    da = cfg.setdefault("dashboard_auth", {})
    if not da.get("session_secret"):
        da["session_secret"] = secrets.token_hex(32)
        save_config(cfg)
    return da["session_secret"]


app.secret_key = ensure_session_secret()


@app.before_request
def enforce_auth():
    """
    Dashboard 按设计要长期暴露公网，所以给所有路由加一层登录门槛（密码 + 可选 Passkey），
    并按来源 IP 做暴力破解锁定。dashboard_auth.enabled 为 false（默认）时不做任何限制，
    适合只在 WireGuard 内网访问、不打算公网暴露的部署方式。
    """
    try:
        cfg = load_config()
    except RuntimeError:
        return  # 还没配置 config.yaml，让后续逻辑走正常的报错路径
    auth_cfg = cfg.get("dashboard_auth", {})
    if not auth_cfg.get("enabled"):
        return

    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint == "static":
        return
    if session.get("authed"):
        return
    if request.path.startswith("/api/"):
        return jsonify({"error": "未登录"}), 401
    return redirect(url_for("login_page"))


@app.route("/login", methods=["GET"])
def login_page():
    return send_from_directory(app.static_folder, "login.html")


@app.route("/login", methods=["POST"])
def login_password():
    cfg = load_config()
    auth_cfg = cfg.get("dashboard_auth", {})
    ip = get_client_ip(request)
    locked, retry_after = _brute_force_guard.is_locked(ip)
    if locked:
        resp = jsonify({"error": f"失败次数过多，请 {retry_after} 秒后再试。"})
        resp.headers["Retry-After"] = str(retry_after)
        return resp, 429

    _brute_force_guard.max_attempts = auth_cfg.get("max_attempts", 5)
    _brute_force_guard.lockout_seconds = auth_cfg.get("lockout_seconds", 900)

    data = request.get_json(silent=True) or {}
    expected_hash = auth_cfg.get("password_hash", "")
    ok = bool(
        expected_hash
        and data.get("username") == auth_cfg.get("username", "")
        and check_password_hash(expected_hash, data.get("password", ""))
    )
    if not ok:
        _brute_force_guard.record_failure(ip)
        return jsonify({"error": "用户名或密码错误"}), 401

    _brute_force_guard.record_success(ip)
    session.clear()
    session["authed"] = True
    session.permanent = True
    return jsonify({"ok": True})


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/webauthn/register/options", methods=["GET"])
def webauthn_register_options():
    cfg = load_config()
    auth_cfg = cfg.get("dashboard_auth", {})
    username = auth_cfg.get("username") or "admin"
    rp_id = wam.rp_id_from_request(request)
    challenge_b64, options_json = wam.registration_options(rp_id, username, auth_cfg.get("passkeys") or [])
    session["webauthn_challenge"] = challenge_b64
    session["webauthn_rp_id"] = rp_id
    return Response(options_json, mimetype="application/json")


@app.route("/api/webauthn/register/verify", methods=["POST"])
def webauthn_register_verify():
    body = request.get_json(silent=True) or {}
    credential = body.get("credential")
    name = (body.get("name") or "").strip() or "未命名 Passkey"
    challenge_b64 = session.pop("webauthn_challenge", None)
    rp_id = session.pop("webauthn_rp_id", None)
    if not credential or not challenge_b64 or not rp_id:
        return jsonify({"error": "注册会话已过期，请重试"}), 400

    credential_json = credential if isinstance(credential, str) else json.dumps(credential)
    try:
        record = wam.verify_registration(credential_json, challenge_b64, rp_id, wam.origin_from_request(request))
    except Exception as e:
        return jsonify({"error": f"验证失败：{e}"}), 400

    cfg = load_config()
    da = cfg.setdefault("dashboard_auth", {})
    passkeys = da.setdefault("passkeys", [])
    passkeys.append({**record, "name": name, "created_at": time.time()})
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/webauthn/passkeys", methods=["GET"])
def webauthn_list_passkeys():
    cfg = load_config()
    passkeys = cfg.get("dashboard_auth", {}).get("passkeys") or []
    return jsonify([
        {"credential_id": p["credential_id"], "name": p.get("name", ""), "created_at": p.get("created_at")}
        for p in passkeys
    ])


@app.route("/api/webauthn/passkeys/<credential_id>", methods=["DELETE"])
def webauthn_delete_passkey(credential_id):
    cfg = load_config()
    da = cfg.setdefault("dashboard_auth", {})
    passkeys = da.get("passkeys") or []
    remaining = [p for p in passkeys if p.get("credential_id") != credential_id]
    if len(remaining) == len(passkeys):
        return jsonify({"error": "未找到该 Passkey"}), 404
    da["passkeys"] = remaining
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/webauthn/login/options", methods=["POST"])
def webauthn_login_options():
    cfg = load_config()
    auth_cfg = cfg.get("dashboard_auth", {})
    ip = get_client_ip(request)
    locked, retry_after = _brute_force_guard.is_locked(ip)
    if locked:
        resp = jsonify({"error": f"失败次数过多，请 {retry_after} 秒后再试。"})
        resp.headers["Retry-After"] = str(retry_after)
        return resp, 429

    rp_id = wam.rp_id_from_request(request)
    challenge_b64, options_json = wam.authentication_options(rp_id, auth_cfg.get("passkeys") or [])
    session["webauthn_challenge"] = challenge_b64
    session["webauthn_rp_id"] = rp_id
    return Response(options_json, mimetype="application/json")


@app.route("/api/webauthn/login/verify", methods=["POST"])
def webauthn_login_verify():
    cfg = load_config()
    auth_cfg = cfg.get("dashboard_auth", {})
    ip = get_client_ip(request)
    locked, retry_after = _brute_force_guard.is_locked(ip)
    if locked:
        resp = jsonify({"error": f"失败次数过多，请 {retry_after} 秒后再试。"})
        resp.headers["Retry-After"] = str(retry_after)
        return resp, 429

    _brute_force_guard.max_attempts = auth_cfg.get("max_attempts", 5)
    _brute_force_guard.lockout_seconds = auth_cfg.get("lockout_seconds", 900)

    body = request.get_json(silent=True) or {}
    credential = body.get("credential")
    challenge_b64 = session.pop("webauthn_challenge", None)
    rp_id = session.pop("webauthn_rp_id", None)
    if not credential or not challenge_b64 or not rp_id:
        _brute_force_guard.record_failure(ip)
        return jsonify({"error": "登录会话已过期，请重试"}), 400

    credential_json = credential if isinstance(credential, str) else json.dumps(credential)
    stored = wam.find_passkey(auth_cfg.get("passkeys") or [], wam.extract_credential_id(credential))
    if not stored:
        _brute_force_guard.record_failure(ip)
        return jsonify({"error": "未知的 Passkey"}), 401

    try:
        new_sign_count = wam.verify_authentication(
            credential_json, challenge_b64, rp_id, wam.origin_from_request(request), stored
        )
    except Exception:
        _brute_force_guard.record_failure(ip)
        return jsonify({"error": "验证失败"}), 401

    stored["sign_count"] = new_sign_count
    save_config(cfg)
    _brute_force_guard.record_success(ip)
    session.clear()
    session["authed"] = True
    session.permanent = True
    return jsonify({"ok": True})


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
    save_config(merged)
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


if __name__ == "__main__":
    t = threading.Thread(target=background_alert_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=1810, debug=True, use_reloader=False)
