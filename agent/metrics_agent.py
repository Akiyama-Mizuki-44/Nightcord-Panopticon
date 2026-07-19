"""
青源（Qingyuan）8.16-beta（内部开发代号 yukikaze）—— 自建系统指标监控 agent，
跑在各面板服务器上（不是 Panopticon 主机），定时采集本机 CPU/内存/磁盘/网络，
push 给 Panopticon 的 /api/metrics/report。整体架构说明见仓库根目录 QINGYUAN.zh-CN.md。

用法：
    pip install -r requirements.txt
    cp agent_config.example.yaml agent_config.yaml   # 填面板名/上报地址/共享密钥
    python metrics_agent.py
生产环境建议配 nightcord-metrics-agent.service（见同目录），systemd 常驻。
"""
import os
import time

import psutil
import requests
import yaml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "agent_config.yaml")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            f"未找到配置文件 {CONFIG_PATH}，请先复制 agent_config.example.yaml 为 agent_config.yaml 并填写"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def collect(prev_net, prev_ts):
    """采集一次快照。网络吞吐由本次/上次 net_io_counters 的差值算出 kbps。"""
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    net = psutil.net_io_counters()
    now = time.time()

    elapsed = max(now - prev_ts, 1e-6)
    net_in_kbps = (net.bytes_recv - prev_net.bytes_recv) / 1024 / elapsed
    net_out_kbps = (net.bytes_sent - prev_net.bytes_sent) / 1024 / elapsed

    sample = {
        "ts": int(now),
        "cpu": cpu,
        "mem": mem,
        "disk": disk,
        "net_in_kbps": round(net_in_kbps, 2),
        "net_out_kbps": round(net_out_kbps, 2),
    }
    return sample, net, now


def main():
    cfg = load_config()
    panel_name = cfg["panel_name"]
    report_url = cfg["report_url"]
    shared_secret = cfg["shared_secret"]
    interval_seconds = cfg.get("interval_seconds", 60)

    prev_net = psutil.net_io_counters()
    prev_ts = time.time()

    print(f"[metrics_agent] 启动，panel={panel_name}，每 {interval_seconds}s 上报一次到 {report_url}")
    while True:
        time.sleep(interval_seconds)
        try:
            sample, prev_net, prev_ts = collect(prev_net, prev_ts)
            sample["panel"] = panel_name
            resp = requests.post(
                report_url,
                json=sample,
                headers={"X-Metrics-Secret": shared_secret},
                timeout=8,
            )
            if resp.status_code != 200:
                print(f"[metrics_agent] 上报失败: HTTP {resp.status_code} {resp.text}")
        except Exception as e:
            # 网络抖动/Panopticon 临时不可达都不该让 agent 退出，下一轮再试
            print(f"[metrics_agent] 上报出错: {e}")


if __name__ == "__main__":
    main()
