"""
告警通知模块：支持飞书自定义机器人 Webhook 与 SMTP 邮件。
两个通道都是"尽力而为"——某一个失败不影响另一个，也不影响主流程。
"""
import smtplib
import time
from email.mime.text import MIMEText
from email.header import Header

import requests


class Notifier:
    def __init__(self, config: dict):
        """
        config 形如:
        {
          "feishu": {"enabled": true, "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"},
          "email": {"enabled": true, "smtp_host": "smtp.qq.com", "smtp_port": 465, "use_ssl": true,
                     "username": "xxx@qq.com", "password": "授权码", "from": "xxx@qq.com", "to": ["a@b.com"]},
          "cooldown_seconds": 600
        }
        """
        self.config = config or {}
        self.cooldown = self.config.get("cooldown_seconds", 600)
        self._last_sent = {}  # key -> timestamp，用于同一告警的冷却去重

    def _should_send(self, key: str) -> bool:
        now = time.time()
        last = self._last_sent.get(key, 0)
        if now - last < self.cooldown:
            return False
        self._last_sent[key] = now
        return True

    def notify(self, key: str, title: str, content: str):
        """key 用于冷却去重，例如 'panel-A-offline' 或 'panel-A-cpu-high'"""
        if not self._should_send(key):
            return
        self._send_feishu(title, content)
        self._send_email(title, content)

    # ---- 飞书自定义机器人 ----
    def _send_feishu(self, title: str, content: str):
        fs = self.config.get("feishu", {})
        if not fs.get("enabled") or not fs.get("webhook"):
            return
        payload = {
            "msg_type": "text",
            "content": {"text": f"【{title}】\n{content}"},
        }
        try:
            requests.post(fs["webhook"], json=payload, timeout=6)
        except Exception as e:
            print(f"[notifier] 飞书推送失败: {e}")

    # ---- SMTP 邮件 ----
    def _send_email(self, title: str, content: str):
        em = self.config.get("email", {})
        if not em.get("enabled"):
            return
        try:
            msg = MIMEText(content, "plain", "utf-8")
            msg["Subject"] = Header(title, "utf-8")
            msg["From"] = em.get("from", em.get("username", ""))
            to_list = em.get("to", [])
            msg["To"] = ", ".join(to_list)

            host, port = em["smtp_host"], em.get("smtp_port", 465)
            if em.get("use_ssl", True):
                server = smtplib.SMTP_SSL(host, port, timeout=8)
            else:
                server = smtplib.SMTP(host, port, timeout=8)
                if em.get("use_tls", True):
                    server.starttls()
            server.login(em["username"], em["password"])
            server.sendmail(msg["From"], to_list, msg.as_string())
            server.quit()
        except Exception as e:
            print(f"[notifier] 邮件推送失败: {e}")


def evaluate_alerts(panel_result: dict, thresholds: dict):
    """
    根据一台面板的采集结果 + 阈值配置，产出告警列表 [(key, title, content), ...]
    thresholds 形如 {"cpu": 90, "mem": 90, "disk": 85}
    """
    alerts = []
    name = panel_result.get("name", "未知面板")

    if panel_result.get("online") is False:
        alerts.append((
            f"{name}-offline",
            "面板离线告警",
            f"面板【{name}】({panel_result.get('url')}) 连接失败：{panel_result.get('error')}",
        ))
        return alerts  # 离线时其它指标无意义，直接返回

    sys = panel_result.get("system") or {}
    net = panel_result.get("network") or {}
    cpu = sys.get("cpuRealUsed")
    if cpu is None and net.get("cpu"):
        cpu = net["cpu"][0]
    if cpu is not None and cpu >= thresholds.get("cpu", 90):
        alerts.append((f"{name}-cpu", "CPU 使用率告警", f"面板【{name}】CPU 使用率 {cpu}%，超过阈值 {thresholds.get('cpu',90)}%"))

    mem_used, mem_total = sys.get("MemRealUsed"), sys.get("MemTotal")
    if mem_used and mem_total:
        mem_pct = mem_used / mem_total * 100
        if mem_pct >= thresholds.get("mem", 90):
            alerts.append((f"{name}-mem", "内存使用率告警", f"面板【{name}】内存使用率 {mem_pct:.1f}%，超过阈值 {thresholds.get('mem',90)}%"))

    for d in (panel_result.get("disk") or []):
        size = d.get("size") or []
        if len(size) >= 4:
            try:
                disk_pct = int(str(size[3]).replace("%", ""))
            except ValueError:
                continue
            if disk_pct >= thresholds.get("disk", 85):
                alerts.append((
                    f"{name}-disk-{d.get('path')}",
                    "磁盘使用率告警",
                    f"面板【{name}】磁盘 {d.get('path')} 使用率 {disk_pct}%，超过阈值 {thresholds.get('disk',85)}%",
                ))
    return alerts
