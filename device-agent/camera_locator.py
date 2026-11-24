#!/usr/bin/env python3
"""
攝影機自動定位模組

攝影機 IP 變動時（換網孔後由 DHCP 配到另一個網段、被改設定等），依 MAC 位址
找回攝影機目前的 IP，並同步更新 go2rtc 串流來源與攝影機網頁代理。

安全原則：只有「原 IP 連不到」且「新 IP 上確認是同一個 MAC」時才會變更，
其餘情況一律不動作；go2rtc.yaml 本身不修改，變更只套用在執行期（PATCH），
並記錄在狀態目錄中，go2rtc 重啟後由 agent 重新套用。
"""

import asyncio
import ipaddress
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import aiohttp

logger = logging.getLogger(__name__)

DNSMASQ_LEASES = os.getenv("DNSMASQ_LEASES", "/var/lib/misc/dnsmasq.leases")
STATE_DIR = Path(os.getenv("CAMERA_STATE_DIR", "/var/lib/mumucam/cameras"))
SWEEP_PORT = 554
SWEEP_CONCURRENCY = 64


def _norm_mac(mac: str) -> str:
    return mac.strip().lower().replace("-", ":")


def _url_host(url: str) -> Optional[str]:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _replace_url_host(url: str, new_host: str) -> str:
    """只替換 URL 中的主機部分，保留帳密、port、路徑與 #fragment"""
    parts = urlsplit(url)
    userinfo, _, hostport = parts.netloc.rpartition("@")
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{userinfo}@{new_host}{port}" if userinfo else f"{new_host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _parse_go2rtc_streams(text: str) -> Dict[str, List[str]]:
    """從 go2rtc.yaml 內容取出 streams 區塊（僅支援 go2rtc 常見的簡單格式）"""
    streams: Dict[str, List[str]] = {}
    in_streams = False
    current = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            in_streams = line.rstrip() == "streams:"
            current = None
            continue
        if not in_streams:
            continue
        key = re.match(r"^ {1,3}([^\s:#-][^:]*):\s*(.*)$", line)
        if key:
            current = key.group(1).strip()
            value = key.group(2).strip().strip("'\"")
            streams[current] = [value] if value else []
            continue
        item = re.match(r"^\s+-\s+(.+)$", line)
        if item and current:
            streams[current].append(item.group(1).strip().strip("'\""))
    return streams


async def _tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


class CameraLocator:
    """依 MAC 位址追蹤攝影機 IP，並讓 go2rtc / 網頁代理跟著更新"""

    def __init__(self, go2rtc_http: str):
        self.go2rtc_http = go2rtc_http.rstrip("/")
        self.macs: Dict[str, str] = {}      # source -> MAC
        self.home_ips: Dict[str, str] = {}  # source -> go2rtc.yaml 中設定的原始 IP
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"[locator] 無法建立狀態目錄 {STATE_DIR}：{e}")

    # -------- 狀態檔 --------

    def _read_state(self, source: str, kind: str) -> Optional[str]:
        try:
            value = (STATE_DIR / f"{source}.{kind}").read_text().strip()
            return value or None
        except Exception:
            return None

    def _write_state(self, source: str, kind: str, value: str):
        path = STATE_DIR / f"{source}.{kind}"
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(value + "\n")
            os.replace(tmp, path)  # 原子替換，網頁代理不會讀到寫一半的檔案
        except Exception as e:
            logger.warning(f"[locator] 寫入狀態檔 {path} 失敗：{e}")

    def register(self, source: str, home_ip: str, mac: Optional[str] = None):
        """登記攝影機；MAC 未指定時沿用先前自動學到的值"""
        self.home_ips[source] = home_ip
        mac = mac or self._read_state(source, "mac")
        if mac:
            self.macs[source] = _norm_mac(mac)

    def saved_ip(self, source: str) -> Optional[str]:
        """上次定位到的 IP（agent 重啟後沿用）"""
        return self._read_state(source, "ip")

    # -------- 定位 --------

    async def _neighbors(self) -> Dict[str, str]:
        """讀取 ARP 表：ip -> MAC（排除失效項目）"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip", "-4", "neigh", "show",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
        except Exception:
            return {}
        result = {}
        for line in out.decode(errors="ignore").splitlines():
            m = re.match(r"^(\S+) dev \S+ lladdr (\S+)", line)
            if m and "FAILED" not in line and "INCOMPLETE" not in line:
                result[m.group(1)] = _norm_mac(m.group(2))
        return result

    async def learn_mac(self, source: str, ip: str) -> Optional[str]:
        """攝影機可連線時，從 ARP 表記下它的 MAC（已知時直接回傳）"""
        if source in self.macs:
            return self.macs[source]
        mac = (await self._neighbors()).get(ip)
        if mac:
            self.macs[source] = mac
            self._write_state(source, "mac", mac)
            logger.info(f"[locator] 記錄 {source} 的 MAC：{mac}（{ip}）")
        return mac

    def _lease_ips(self, mac: str) -> List[str]:
        """dnsmasq 租約中此 MAC 的 IP（格式：到期時間 MAC IP 主機名 client-id）"""
        try:
            lines = Path(DNSMASQ_LEASES).read_text().splitlines()
        except Exception:
            return []
        return [p[2] for p in (line.split() for line in lines)
                if len(p) >= 3 and _norm_mac(p[1]) == mac]

    async def _camera_subnets(self) -> List[ipaddress.IPv4Network]:
        """要掃描的網段：各攝影機原始 IP 所在介面的網段（限 /24 以內，避免掃描過大範圍）"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip", "-4", "-o", "addr", "show",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
        except Exception:
            return []
        homes = [ipaddress.ip_address(ip) for ip in self.home_ips.values()]
        nets = []
        for m in re.finditer(r"inet (\S+)", out.decode(errors="ignore")):
            net = ipaddress.ip_interface(m.group(1)).network
            if net.prefixlen >= 24 and any(h in net for h in homes) and net not in nets:
                nets.append(net)
        return nets

    async def _verify(self, ip: str, mac: str) -> bool:
        """確認 ip 上的裝置就是這個 MAC，且 RTSP port 有開"""
        if not await _tcp_open(ip, SWEEP_PORT, 3):
            return False
        return (await self._neighbors()).get(ip) == mac

    async def locate(self, source: str) -> Optional[str]:
        """依 MAC 找出攝影機目前的 IP；找不到或未知 MAC 時回傳 None"""
        mac = self.macs.get(source)
        if not mac:
            logger.warning(f"[locator] {source} 尚未記錄 MAC，無法自動定位")
            return None

        # 1. DHCP 租約與 ARP 表（快速）
        neighbors = await self._neighbors()
        candidates = self._lease_ips(mac) + [ip for ip, m in neighbors.items() if m == mac]
        for ip in dict.fromkeys(candidates):
            if await self._verify(ip, mac):
                return ip

        # 2. 掃描攝影機網段，讓 ARP 表補齊後再比對 MAC
        nets = await self._camera_subnets()
        logger.info(f"[locator] 掃描 {[str(n) for n in nets]} 尋找 {source}（{mac}）")
        sem = asyncio.Semaphore(SWEEP_CONCURRENCY)

        async def poke(host: str):
            async with sem:
                await _tcp_open(host, SWEEP_PORT, 0.5)

        await asyncio.gather(*(poke(str(h)) for net in nets for h in net.hosts()))
        for ip, m in (await self._neighbors()).items():
            if m == mac and await self._verify(ip, mac):
                return ip
        return None

    # -------- 套用新 IP --------

    async def apply(self, source: str, ip: str) -> bool:
        """記錄 source 的目前 IP，並更新 go2rtc 串流（網頁代理每次連線會讀取狀態檔）"""
        self._write_state(source, "ip", ip)
        return await self.sync_go2rtc(source, ip)

    async def sync_go2rtc(self, source: str, ip: str) -> bool:
        """
        讓 go2rtc 中所有指向此攝影機原始 IP 的串流改用 ip。
        已一致時不動作；go2rtc 重啟後（回到 yaml 設定）會重新套用。
        """
        home = self.home_ips.get(source)
        if not home:
            return False
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.go2rtc_http}/api/config") as resp:
                    config_streams = _parse_go2rtc_streams(await resp.text())
                async with session.get(f"{self.go2rtc_http}/api/streams") as resp:
                    runtime = await resp.json(content_type=None) or {}

                for name, urls in config_streams.items():
                    if not any(_url_host(u) == home for u in urls):
                        continue
                    wanted = [_replace_url_host(u, ip) if _url_host(u) == home else u for u in urls]
                    producers = (runtime.get(name) or {}).get("producers") or []
                    if [_url_host(p.get("url", "")) for p in producers] == [_url_host(u) for u in wanted]:
                        continue
                    params = [("name", name)] + [("src", u) for u in wanted]
                    async with session.patch(f"{self.go2rtc_http}/api/streams", params=params) as resp:
                        if resp.status != 200:
                            logger.error(f"[locator] 更新 go2rtc 串流 {name} 失敗：HTTP {resp.status}")
                            return False
                    logger.info(f"[locator] go2rtc 串流 {name} 已改用 {ip}")
            return True
        except Exception as e:
            logger.error(f"[locator] 更新 go2rtc 失敗：{e}")
            return False
