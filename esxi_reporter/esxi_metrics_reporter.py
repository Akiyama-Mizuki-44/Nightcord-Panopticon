"""
ESXi 指标上报脚本 —— 见仓库根目录 ESXI_QINGYUAN_MONITORING.zh-CN.md。

不在 ESXi 本机装任何东西（ESXi 精简系统装不了 python3 venv / systemd 常驻），
而是跑在已经能连到 ESXi 的机器上（比如 FreePBX），用 pyVmomi 连 vSphere API
查 CPU/内存/磁盘，代 ESXi"上报"给 Panopticon 已有的 /api/metrics/report 接口，
复用青源（Qingyuan）那一整套历史趋势图卡片，不需要改 Panopticon 任何代码。

用法：
    pip install -r requirements.txt
    cp esxi_reporter_config.example.yaml esxi_reporter_config.yaml   # 填 ESXi 主机/账号/密码
    python esxi_metrics_reporter.py
生产环境建议配 nightcord-esxi-reporter.service，systemd 常驻。
"""
import os
import ssl
import time

import requests
import yaml
from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim  # noqa: F401  保留导入，方便以后扩展查具体 vim 对象类型时用

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "esxi_reporter_config.yaml")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            f"未找到配置文件 {CONFIG_PATH}，请先复制 esxi_reporter_config.example.yaml 为 "
            "esxi_reporter_config.yaml 并填写"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def collect_one(cfg):
    """连一次 ESXi 主机，取 CPU/内存/磁盘使用率。网络吞吐暂不采集，见文档「已知限制」。"""
    ctx = ssl._create_unverified_context()  # ESXi 管理接口大多是自签证书，跟面板同理不做校验
    si = SmartConnect(host=cfg["host"], user=cfg["user"], pwd=cfg["password"], sslContext=ctx)
    try:
        host = si.content.rootFolder.childEntity[0].hostFolder.childEntity[0].host[0]
        qs = host.summary.quickStats
        hw = host.summary.hardware
        cpu_pct = qs.overallCpuUsage / (hw.numCpuCores * hw.cpuMhz) * 100
        mem_pct = qs.overallMemoryUsage / (hw.memorySize / 1024 / 1024) * 100

        cap = free = 0
        for ds in host.datastore:
            cap += ds.summary.capacity
            free += ds.summary.freeSpace
        disk_pct = (1 - free / cap) * 100 if cap else 0

        return cpu_pct, mem_pct, disk_pct
    finally:
        Disconnect(si)


def report_one(panel_name, cfg, report_url, shared_secret):
    cpu, mem, disk = collect_one(cfg)
    resp = requests.post(
        report_url,
        json={
            "panel": panel_name,
            "ts": int(time.time()),
            "cpu": round(cpu, 1),
            "mem": round(mem, 1),
            "disk": round(disk, 1),
            # PerformanceManager 查网络吞吐比前三项麻烦得多，先占位成 0——
            # 不影响卡片显示，前端只是不画网络那条线，见文档「已知限制」。
            "net_in_kbps": 0,
            "net_out_kbps": 0,
        },
        headers={"X-Metrics-Secret": shared_secret},
        timeout=6,
    )
    if resp.status_code != 200:
        print(f"[esxi_metrics_reporter] {panel_name} 上报失败: HTTP {resp.status_code} {resp.text}")


def main():
    cfg = load_config()
    report_url = cfg["report_url"]
    shared_secret = cfg["shared_secret"]
    esxi_hosts = cfg["esxi_hosts"]
    interval_seconds = cfg.get("interval_seconds", 60)
    if interval_seconds >= 180:
        # Panopticon 那边 AGENT_STALE_SECONDS 是 180s，报得比这个慢就会一直被判定离线
        print(f"[esxi_metrics_reporter] 警告: interval_seconds={interval_seconds} 太大，"
              "Panopticon 会认为这些主机一直离线（阈值 180s）")

    print(f"[esxi_metrics_reporter] 启动，{len(esxi_hosts)} 台 ESXi 主机，"
          f"每 {interval_seconds}s 上报一次到 {report_url}")

    def report_round():
        for panel_name, host_cfg in esxi_hosts.items():
            try:
                report_one(panel_name, host_cfg, report_url, shared_secret)
            except Exception as e:
                # 单台 ESXi 连不上/查询失败都不该连累其它主机或让脚本退出，下一轮再试
                print(f"[esxi_metrics_reporter] {panel_name} 上报出错: {e}")

    # 跟 agent/metrics_agent.py 一样：启动后立刻报一次，不用等第一个 interval 过去，
    # 装完/重启完就能马上在 Panopticon 主看板上看到卡片。
    report_round()
    while True:
        time.sleep(interval_seconds)
        report_round()


if __name__ == "__main__":
    main()
