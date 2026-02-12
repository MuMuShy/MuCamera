#!/usr/bin/env python3
"""
System Monitor for Raspberry Pi
Reads system information from /proc, /sys without requiring psutil.
"""

import os
import time
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SystemMonitor:
    """Collect system info from /proc and /sys (Linux only)"""

    def __init__(self):
        self._prev_cpu_times = None
        self._prev_net_counters = None
        self._prev_sample_time = None

    def collect(self) -> Dict:
        """Collect all system information and return as dict"""
        info = {}
        try:
            info["cpu"] = self._get_cpu()
        except Exception as e:
            logger.debug(f"[sysmon] CPU read error: {e}")
            info["cpu"] = {}
        try:
            info["temperature"] = self._get_temperature()
        except Exception as e:
            logger.debug(f"[sysmon] Temperature read error: {e}")
            info["temperature"] = {}
        try:
            info["memory"] = self._get_memory()
        except Exception as e:
            logger.debug(f"[sysmon] Memory read error: {e}")
            info["memory"] = {}
        try:
            info["disk"] = self._get_disk()
        except Exception as e:
            logger.debug(f"[sysmon] Disk read error: {e}")
            info["disk"] = {}
        try:
            info["network"] = self._get_network()
        except Exception as e:
            logger.debug(f"[sysmon] Network read error: {e}")
            info["network"] = {}
        try:
            info["system"] = self._get_system_info()
        except Exception as e:
            logger.debug(f"[sysmon] System info read error: {e}")
            info["system"] = {}
        return info

    # ------ CPU ------

    def _read_cpu_times(self):
        """Read aggregate CPU times from /proc/stat"""
        try:
            with open("/proc/stat") as f:
                line = f.readline()  # first line = aggregate
            parts = line.split()
            # user, nice, system, idle, iowait, irq, softirq, steal
            times = [int(p) for p in parts[1:9]]
            return times
        except Exception:
            return None

    def _get_cpu(self) -> Dict:
        cur = self._read_cpu_times()
        if cur is None:
            return {}

        result = {"cores": os.cpu_count() or 0}

        if self._prev_cpu_times is not None:
            deltas = [c - p for c, p in zip(cur, self._prev_cpu_times)]
            total = sum(deltas)
            if total > 0:
                idle = deltas[3] + deltas[4]  # idle + iowait
                usage = 100.0 * (1.0 - idle / total)
                result["usage_percent"] = round(usage, 1)

        self._prev_cpu_times = cur

        # Load averages
        try:
            with open("/proc/loadavg") as f:
                parts = f.read().split()
            result["load_1m"] = float(parts[0])
            result["load_5m"] = float(parts[1])
            result["load_15m"] = float(parts[2])
        except Exception:
            pass

        return result

    # ------ Temperature ------

    def _get_temperature(self) -> Dict:
        temp = None
        # Try thermal zone
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                raw = int(f.read().strip())
            temp = round(raw / 1000.0, 1)
        except Exception:
            pass

        # Fallback: vcgencmd (Pi specific)
        if temp is None:
            try:
                import subprocess
                out = subprocess.check_output(
                    ["vcgencmd", "measure_temp"],
                    timeout=2, stderr=subprocess.DEVNULL
                ).decode()
                # temp=42.0'C
                temp = float(out.split("=")[1].split("'")[0])
            except Exception:
                pass

        if temp is not None:
            return {"cpu_temp": temp}
        return {}

    # ------ Memory ------

    def _get_memory(self) -> Dict:
        mem = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    key = parts[0].rstrip(":")
                    val = int(parts[1])  # kB
                    if key == "MemTotal":
                        mem["total_mb"] = round(val / 1024, 1)
                    elif key == "MemAvailable":
                        mem["available_mb"] = round(val / 1024, 1)
                    elif key == "Buffers":
                        mem["buffers_mb"] = round(val / 1024, 1)
                    elif key == "Cached":
                        mem["cached_mb"] = round(val / 1024, 1)
        except Exception:
            return {}

        if "total_mb" in mem and "available_mb" in mem:
            used = mem["total_mb"] - mem["available_mb"]
            mem["used_mb"] = round(used, 1)
            mem["usage_percent"] = round(100.0 * used / mem["total_mb"], 1) if mem["total_mb"] > 0 else 0
        return mem

    # ------ Disk ------

    def _get_disk(self) -> Dict:
        try:
            st = os.statvfs("/")
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - free
            total_gb = round(total / (1024 ** 3), 2)
            used_gb = round(used / (1024 ** 3), 2)
            free_gb = round(free / (1024 ** 3), 2)
            usage_pct = round(100.0 * used / total, 1) if total > 0 else 0
            return {
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": free_gb,
                "usage_percent": usage_pct,
            }
        except Exception:
            return {}

    # ------ Network ------

    def _get_network(self) -> Dict:
        now = time.time()
        counters = {}
        try:
            with open("/proc/net/dev") as f:
                lines = f.readlines()[2:]  # skip header
            for line in lines:
                parts = line.split()
                iface = parts[0].rstrip(":")
                if iface == "lo":
                    continue
                rx_bytes = int(parts[1])
                tx_bytes = int(parts[9])
                counters[iface] = {"rx_bytes": rx_bytes, "tx_bytes": tx_bytes}
        except Exception:
            return {}

        result = {"interfaces": {}}

        for iface, cur in counters.items():
            entry = {
                "rx_bytes": cur["rx_bytes"],
                "tx_bytes": cur["tx_bytes"],
            }
            if self._prev_net_counters and iface in self._prev_net_counters and self._prev_sample_time:
                dt = now - self._prev_sample_time
                if dt > 0:
                    prev = self._prev_net_counters[iface]
                    entry["rx_rate_kbps"] = round((cur["rx_bytes"] - prev["rx_bytes"]) / dt / 1024, 1)
                    entry["tx_rate_kbps"] = round((cur["tx_bytes"] - prev["tx_bytes"]) / dt / 1024, 1)
            result["interfaces"][iface] = entry

        self._prev_net_counters = counters
        self._prev_sample_time = now
        return result

    # ------ System Info ------

    def _get_system_info(self) -> Dict:
        info = {}
        # Uptime
        try:
            with open("/proc/uptime") as f:
                uptime_sec = float(f.read().split()[0])
            info["uptime_seconds"] = int(uptime_sec)
        except Exception:
            pass

        # Pi model
        try:
            with open("/proc/device-tree/model") as f:
                model = f.read().strip().rstrip("\x00")
            info["model"] = model
        except Exception:
            pass

        # OS info
        try:
            os_info = {}
            with open("/etc/os-release") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        os_info[k] = v.strip('"')
            info["os_name"] = os_info.get("PRETTY_NAME", "")
        except Exception:
            pass

        # Hostname
        try:
            info["hostname"] = os.uname().nodename
        except Exception:
            pass

        # Kernel
        try:
            info["kernel"] = os.uname().release
        except Exception:
            pass

        return info


# --------------- Standalone test ---------------
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.DEBUG)
    mon = SystemMonitor()
    # First sample (CPU delta needs 2 reads)
    mon.collect()
    time.sleep(1)
    data = mon.collect()
    print(json.dumps(data, indent=2, ensure_ascii=False))
