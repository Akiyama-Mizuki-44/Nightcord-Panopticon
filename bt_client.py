"""
宝塔面板 API 客户端
签名算法参考宝塔官方文档 (docs.bt.cn/user-guide/config/common/panel-api)：
  request_time  = 当前 Unix 时间戳
  request_token = md5( str(request_time) + md5(api_sk) )
所有请求统一使用 POST，参数放在 query string 中（官方 demo 的常见用法）。
"""
import hashlib
import socket
import time
from urllib.parse import urlparse

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

DEFAULT_TIMEOUT = 8
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BT-Dashboard/1.0"


class BTPanelError(Exception):
    pass


class BTClient:
    def __init__(self, name: str, base_url: str, api_key: str, verify_ssl: bool = False):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.verify_ssl = verify_ssl
        # collect_all() 一次要打好几个请求，用同一个 Session 复用连接和 cookie，
        # 符合宝塔官方文档"注意事项"里"请保存 cookie，并在每次请求时附上 cookie"的要求。
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.session.verify = verify_ssl

    def _sign(self):
        request_time = str(int(time.time()))
        sk_md5 = hashlib.md5(self.api_key.encode("utf-8")).hexdigest()
        request_token = hashlib.md5((request_time + sk_md5).encode("utf-8")).hexdigest()
        return {"request_time": request_time, "request_token": request_token}

    def _call(self, path: str, extra: dict = None):
        params = self._sign()
        if extra:
            params.update(extra)
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.post(url, params=params, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise BTPanelError(f"[{self.name}] 请求 {path} 失败: {e}")

    # ---- 系统状态 ----
    def system_total(self):
        """CPU / 内存 / 系统信息"""
        return self._call("/system?action=GetSystemTotal")

    def network(self):
        """网络流量、负载"""
        return self._call("/system?action=GetNetWork")

    def disk_info(self):
        """磁盘分区使用情况"""
        return self._call("/system?action=GetDiskInfo")

    # ---- 网站 ----
    def sites(self, limit: int = 100):
        return self._call("/data?action=getData&table=sites", {"limit": limit, "p": 1})

    # ---- 数据库 ----
    def databases(self, limit: int = 100):
        return self._call("/data?action=getData&table=databases", {"limit": limit, "p": 1})

    def _resolve_external_ip(self):
        """宝塔面板没有 agent 跑在本机，拿不到真内网 IP；只能把面板 url 的 host 解析成 IP 当"外网 IP"参考。"""
        host = urlparse(self.base_url).hostname
        if not host:
            return None
        try:
            return socket.gethostbyname(host)
        except OSError:
            return None

    def collect_all(self):
        """汇总一台面板的所有信息，单项失败不影响其它项"""
        result = {
            "name": self.name,
            "url": self.base_url,
            "online": True,
            "error": None,
            "ip_external": self._resolve_external_ip(),
            "ip_internal": None,  # 宝塔面板没部署青源 agent，拿不到真内网 IP
        }

        def safe(fn, key):
            try:
                result[key] = fn()
            except Exception as e:
                result[key] = None
                result["online"] = False
                result["error"] = str(e)

        safe(self.system_total, "system")
        safe(self.network, "network")
        safe(self.disk_info, "disk")
        safe(self.sites, "sites")
        safe(self.databases, "databases")
        self.session.close()
        return result
