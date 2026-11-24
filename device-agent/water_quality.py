#!/usr/bin/env python3
"""
水質監測站（JNC CB 主機）讀取模組

CB 接在 Pi 的 eth1（192.168.0.88），提供網頁介面：
  get_ChName.csv  : 頻道數,..(共 10 欄表頭).., 然後每頻道 3 欄 (類型, 名稱[URL 編碼], 單位)
  get_ChValue.csv : 時間, ..(10 欄表頭).., 然後每頻道 4 欄 (類型, 數值, 空白, 第二數值)
類型 1 = 類比量測值，類型 4 = 開關狀態（ON/OFF）。

只做 HTTP GET 讀取，不會修改 CB 任何設定。
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional
from urllib.parse import unquote

import aiohttp

logger = logging.getLogger(__name__)

WQ_HOST = os.getenv("WQ_HOST", "192.168.0.88")
WQ_POLL_SECONDS = int(os.getenv("WQ_POLL_SECONDS", "60"))
WQ_ENABLED = os.getenv("WQ_ENABLED", "true").lower() == "true"
LTE_STATUS_FILE = os.getenv("LTE_STATUS_FILE", "/run/mumucam/lte-status")

HEADER_FIELDS = 10
# 輔助頻道（感測器狀態/斜率/保養），不放在主要數值列表
AUX_KEYWORDS = ("狀態", "斜率", "保養", "清池")


def _parse_names(text: str) -> List[Dict]:
    f = [x.strip() for x in text.strip().split(",")]
    count = int(f[0])
    chans = []
    for i in range(count):
        base = HEADER_FIELDS + i * 3
        if base + 2 >= len(f) + 1:
            break
        chans.append({
            "type": f[base],
            "name": unquote(f[base + 1]),
            "unit": unquote(f[base + 2]) if base + 2 < len(f) else "",
        })
    return chans


def _parse_values(text: str, chans: List[Dict]) -> Dict:
    f = [x.strip() for x in text.strip().split(",")]
    ts = f[0].replace("&nbsp;", " ")
    values = []
    for i, ch in enumerate(chans):
        base = 1 + HEADER_FIELDS + i * 4
        if base + 1 >= len(f):
            break
        raw = f[base + 1]
        try:
            val = float(raw)
        except ValueError:
            val = None
        values.append({
            "name": ch["name"],
            "unit": "" if ch["type"] == "4" else ch["unit"],
            "value": val,
            "text": raw,
            "aux": any(k in ch["name"] for k in AUX_KEYWORDS) or ch["type"] == "4",
        })
    return {"device_time": ts, "channels": values}


def read_lte_status() -> Dict:
    """讀取 4G watchdog 寫的狀態檔（key=value）"""
    try:
        with open(LTE_STATUS_FILE, encoding="utf-8") as fh:
            return dict(line.rstrip("\n").split("=", 1) for line in fh if "=" in line)
    except Exception:
        return {}


class WaterQualityPoller:
    def __init__(self):
        self.latest: Dict = {"enabled": WQ_ENABLED, "online": False, "host": WQ_HOST}
        self._chans: Optional[List[Dict]] = None
        self._names_ts = 0.0

    async def _get(self, session: aiohttp.ClientSession, path: str) -> str:
        async with session.get(f"http://{WQ_HOST}/{path}") as r:
            r.raise_for_status()
            return (await r.read()).decode("utf-8-sig", "ignore")

    async def poll_once(self):
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            # 頻道名稱很少變動，每 10 分鐘更新一次
            if self._chans is None or time.time() - self._names_ts > 600:
                self._chans = _parse_names(await self._get(s, "get_ChName.csv"))
                self._names_ts = time.time()
            data = _parse_values(await self._get(s, "get_ChValue.csv"), self._chans)
        self.latest = {
            "enabled": True,
            "online": True,
            "host": WQ_HOST,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            **data,
        }

    async def run(self):
        if not WQ_ENABLED:
            logger.info("[wq] 水質讀取未啟用")
            return
        logger.info(f"[wq] 開始讀取水質監測站 {WQ_HOST}（每 {WQ_POLL_SECONDS}s）")
        fails = 0
        while True:
            try:
                await self.poll_once()
                if fails:
                    logger.info("[wq] 水質監測站恢復連線")
                fails = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                fails += 1
                self.latest = {**self.latest, "online": False, "error": str(e)[:200]}
                if fails in (1, 5) or fails % 60 == 0:
                    logger.warning(f"[wq] 讀取失敗（{fails} 次）：{e}")
            await asyncio.sleep(WQ_POLL_SECONDS)


if __name__ == "__main__":
    import json
    p = WaterQualityPoller()
    asyncio.run(p.poll_once())
    print(json.dumps(p.latest, ensure_ascii=False, indent=1))
    print(json.dumps(read_lte_status(), ensure_ascii=False, indent=1))
