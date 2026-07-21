"""
告警通知模块：支持飞书自定义机器人 Webhook 与 SMTP 邮件。
两个通道都是"尽力而为"——某一个失败不影响另一个，也不影响主流程。
"""
import smtplib
import time
from datetime import datetime
from email.mime.text import MIMEText
from email.header import Header

import requests

UNKNOWN_IP = "未知"


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
        self._last_sent = {}  # key -> 上次通知时间戳，问题解决后又复发时用来防抖
        self._active = set()  # 当前"已经通知过、问题还没解决"的 key，同一个问题只发一次

    def update_config(self, config: dict):
        """巡检线程每轮都会重新读一次 config.yaml，用这个刷新配置，
        而不是每轮都 new 一个 Notifier ——不然 _active/_last_sent 全被清空，
        等于每轮都当成新问题重新通知一遍，跟"发一遍就够了"背道而驰。
        """
        self.config = config or {}
        self.cooldown = self.config.get("cooldown_seconds", 600)

    def _should_send(self, key: str) -> bool:
        if key in self._active:
            return False  # 这个问题还没解决，之前已经通知过了，不用再发
        now = time.time()
        last = self._last_sent.get(key, 0)
        if now - last < self.cooldown:
            return False  # 刚解决又复发（抖动）也别刷屏，等冷却过了才重新算一次新问题
        self._last_sent[key] = now
        self._active.add(key)
        return True

    def resolve(self, still_active_keys: set):
        """
        每轮巡检结束时调用：这轮里已经不再出现的 key 说明问题解决了，
        从"已通知"名单里摘掉，以后再犯还能收到通知（而不是永远只通知一次）。
        """
        self._active &= still_active_keys

    def notify(self, alert: dict):
        """
        alert 形如:
        {
          "key": "面板A-cpu",          # 用于冷却去重
          "server": "面板A",
          "alert_type": "CPU高占用告警",
          "content": "最近5分钟内机器CPU平均占用率为89.28%，高于告警值60%",
          "ip_external": "18.130.178.188" 或 None,
          "ip_internal": "172.31.47.45" 或 None,
        }
        """
        if not self._should_send(alert["key"]):
            return
        self._send_feishu(alert)
        self._send_email(alert)

    # ---- 飞书自定义机器人 ----
    @staticmethod
    def _build_feishu_card(alert: dict, template: str = "red"):
        """
        飞书卡片 Schema 2.0 结构：card.header 是那条带颜色的标题栏，
        card.body.elements 才是正文。只塞一个不带 header 的 markdown 元素虽然飞书也认，
        但看起来就是一段白底文字，没有"卡片"的既视感——header 才是让它像卡片的关键。
        """
        ip_ext = alert.get("ip_external") or UNKNOWN_IP
        ip_int = alert.get("ip_internal") or UNKNOWN_IP
        sent_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        markdown = "\n\n".join([
            f">服务器：{alert['server']}",
            f">IP地址：{ip_ext}(外) {ip_int}(内)",
            f">发送时间：{sent_at}",
            f">通知类型：{alert['alert_type']}",
            f">告警内容：\n{alert['content']}<at id=all></at>",
        ])
        return {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "config": {"update_multi": True},
                "header": {
                    "title": {"tag": "plain_text", "content": alert["alert_type"]},
                    "template": template,
                },
                "body": {
                    "direction": "vertical",
                    "elements": [{"tag": "markdown", "content": markdown}],
                },
            },
        }

    def _send_feishu(self, alert: dict):
        fs = self.config.get("feishu", {})
        if not fs.get("enabled") or not fs.get("webhook"):
            return
        try:
            requests.post(fs["webhook"], json=self._build_feishu_card(alert), timeout=6)
        except Exception as e:
            print(f"[notifier] 飞书推送失败: {e}")

    def send_feishu_test(self):
        """设置页"发送测试通知"按钮用：不走冷却，同步返回是否真的发成功了（而不只是 HTTP 200）。"""
        fs = self.config.get("feishu", {})
        webhook = fs.get("webhook")
        if not fs.get("enabled") or not webhook:
            return False, "还没启用飞书推送，或者没填 Webhook 地址"

        alert = {
            "server": "测试服务器",
            "alert_type": "测试通知",
            "content": "这是一条来自 Nightcord Panopticon 设置页的测试消息，用来确认飞书 Webhook 是否配置正确。",
            "ip_external": "203.0.113.1",
            "ip_internal": "10.0.0.1",
        }
        try:
            # 用蓝色标题栏跟真实告警（红色）区分开，一看颜色就知道这是不是真事故
            resp = requests.post(webhook, json=self._build_feishu_card(alert, template="blue"), timeout=6)
        except Exception as e:
            return False, f"请求失败：{e}"

        try:
            body = resp.json()
        except ValueError:
            return False, f"HTTP {resp.status_code}，响应不是合法 JSON：{resp.text[:200]}"

        # 飞书自定义机器人即使 webhook 地址错/参数错也大多返回 HTTP 200，真正的成败要看 body 里的 code
        if resp.status_code == 200 and body.get("code", body.get("StatusCode", -1)) == 0:
            return True, "测试消息已发送，去飞书群里看看卡片是不是这样喵"
        return False, f"飞书返回：{body.get('msg') or body}"

    # ---- SMTP 邮件 ----
    def _send_email(self, alert: dict):
        em = self.config.get("email", {})
        if not em.get("enabled"):
            return
        title = alert["alert_type"]
        content = f"服务器：{alert['server']}\n{alert['content']}"
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
    根据一台面板（宝塔面板 / Nightcord-Status）的采集结果 + 阈值配置，产出告警列表。
    thresholds 形如 {"cpu": 90, "mem": 90, "disk": 85}
    每条告警是一个 dict，字段见 Notifier.notify() 的文档字符串。
    """
    alerts = []
    name = panel_result.get("name", "未知面板")
    ip_external = panel_result.get("ip_external")
    ip_internal = panel_result.get("ip_internal")

    def make(suffix, alert_type, content):
        return {
            "key": f"{name}-{suffix}",
            "server": name,
            "alert_type": alert_type,
            "content": content,
            "ip_external": ip_external,
            "ip_internal": ip_internal,
        }

    if panel_result.get("online") is False:
        alerts.append(make(
            "offline",
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
        alerts.append(make("cpu", "CPU高占用告警", f"最近一次采样机器CPU占用率为{cpu}%，高于告警值{thresholds.get('cpu',90)}%"))

    mem_used, mem_total = sys.get("MemRealUsed"), sys.get("MemTotal")
    if mem_used and mem_total:
        mem_pct = mem_used / mem_total * 100
        if mem_pct >= thresholds.get("mem", 90):
            alerts.append(make("mem", "内存高占用告警", f"最近一次采样机器内存占用率为{mem_pct:.2f}%，高于告警值{thresholds.get('mem',90)}%"))

    for d in (panel_result.get("disk") or []):
        size = d.get("size") or []
        if len(size) >= 4:
            try:
                disk_pct = int(str(size[3]).replace("%", ""))
            except ValueError:
                continue
            if disk_pct >= thresholds.get("disk", 85):
                path = d.get("path")
                alerts.append(make(
                    f"disk-{path}",
                    "磁盘余量告警",
                    f"挂载目录【{path}】的磁盘已使用容量为{disk_pct}%，大于告警值{thresholds.get('disk',85)}%",
                ))
    return alerts


def evaluate_agent_alerts(panel_name: str, sample: dict, thresholds: dict, ip_external=None, ip_internal=None):
    """
    根据青源（Qingyuan）自建 agent 上报的最新一条样本 + 阈值配置，产出告警列表。
    sample 形如 metrics_store.get_latest() 的返回值：{"ts","cpu","mem","disk","net_in_kbps","net_out_kbps"}
    """
    alerts = []
    if not sample:
        return alerts

    def make(suffix, alert_type, content):
        return {
            "key": f"{panel_name}-{suffix}",
            "server": panel_name,
            "alert_type": alert_type,
            "content": content,
            "ip_external": ip_external,
            "ip_internal": ip_internal,
        }

    cpu, mem, disk = sample.get("cpu"), sample.get("mem"), sample.get("disk")
    if cpu is not None and cpu >= thresholds.get("cpu", 90):
        alerts.append(make("agent-cpu", "CPU高占用告警", f"最近一次采样机器CPU占用率为{cpu:.2f}%，高于告警值{thresholds.get('cpu',90)}%"))
    if mem is not None and mem >= thresholds.get("mem", 90):
        alerts.append(make("agent-mem", "内存高占用告警", f"最近一次采样机器内存占用率为{mem:.2f}%，高于告警值{thresholds.get('mem',90)}%"))
    if disk is not None and disk >= thresholds.get("disk", 85):
        alerts.append(make("agent-disk-/", "磁盘余量告警", f"挂载目录【/】的磁盘已使用容量为{disk:.2f}%，大于告警值{thresholds.get('disk',85)}%"))
    return alerts
