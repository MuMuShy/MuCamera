import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

try:
    import serial  # pyserial
except Exception:  # 沒裝 pyserial 也不要炸主程式
    serial = None

GPS_DEV = "/dev/sim7600-gps"
GPS_BAUD = 115200

@dataclass
class GPSState:
    enabled: bool = True          # 你可以用 env 控制
    status: str = "disabled"      # disabled/searching/fixed/lost/error
    lat: float | None = None
    lon: float | None = None
    satellites: int | None = None
    hdop: float | None = None
    last_fix_utc: str | None = None
    last_sentence_utc: str | None = None
    error: str | None = None

def _utc_now():
    return datetime.now(timezone.utc).isoformat()

def _nmea_to_decimal(ddmm: str, hemi: str) -> float | None:
    if not ddmm or not hemi:
        return None
    try:
        dot = ddmm.find(".")
        if dot < 0:
            return None
        deg_len = 2 if dot <= 4 else 3
        deg = float(ddmm[:deg_len])
        minutes = float(ddmm[deg_len:])
        val = deg + minutes / 60.0
        if hemi in ("S", "W"):
            val = -val
        return val
    except Exception:
        return None

def parse_gga(line: str):
    p = line.split(",")
    if len(p) < 10:
        return None
    fix = p[6]
    sats = p[7]
    hdop = p[8]
    lat = _nmea_to_decimal(p[2], p[3])
    lon = _nmea_to_decimal(p[4], p[5])
    return {
        "fix": fix,
        "lat": lat,
        "lon": lon,
        "sats": int(sats) if sats.isdigit() else None,
        "hdop": float(hdop) if hdop else None,
    }

def parse_rmc(line: str):
    p = line.split(",")
    if len(p) < 7:
        return None
    status = p[2]  # A/V
    lat = _nmea_to_decimal(p[3], p[4])
    lon = _nmea_to_decimal(p[5], p[6])
    return {"status": status, "lat": lat, "lon": lon}

class GPSReader(threading.Thread):
    def __init__(self, state: GPSState, lock: threading.Lock, retry_sec: float = 3.0):
        super().__init__(daemon=True)
        self.state = state
        self.lock = lock
        self.retry_sec = retry_sec

    def run(self):
        # GPS 不可用也不要影響主程式：永遠在背景自我修復
        while True:
            if not self.state.enabled:
                with self.lock:
                    self.state.status = "disabled"
                    self.state.error = None
                time.sleep(5)
                continue

            if serial is None:
                with self.lock:
                    self.state.status = "disabled"
                    self.state.error = "pyserial not installed"
                time.sleep(10)
                continue

            try:
                # 不等待 device：打不開就算了，稍後再試
                with serial.Serial(GPS_DEV, GPS_BAUD, timeout=1) as ser:
                    with self.lock:
                        if self.state.status in ("disabled", "error"):
                            self.state.status = "searching"
                            self.state.error = None

                    while True:
                        raw = ser.readline()
                        if not raw:
                            continue
                        line = raw.decode(errors="ignore").strip()
                        if not line.startswith("$"):
                            continue

                        now = _utc_now()
                        with self.lock:
                            self.state.last_sentence_utc = now

                        if line.startswith("$GPGGA"):
                            g = parse_gga(line)
                            if not g:
                                continue
                            fix_ok = g["fix"] in ("1", "2")
                            with self.lock:
                                if fix_ok and g["lat"] is not None and g["lon"] is not None:
                                    self.state.status = "fixed"
                                    self.state.lat = g["lat"]
                                    self.state.lon = g["lon"]
                                    self.state.satellites = g["sats"]
                                    self.state.hdop = g["hdop"]
                                    self.state.last_fix_utc = now
                                else:
                                    self.state.status = "lost" if self.state.last_fix_utc else "searching"

                        elif line.startswith("$GPRMC"):
                            r = parse_rmc(line)
                            if not r:
                                continue
                            with self.lock:
                                if r["status"] == "A" and r["lat"] is not None and r["lon"] is not None:
                                    self.state.status = "fixed"
                                    self.state.lat = r["lat"]
                                    self.state.lon = r["lon"]
                                    self.state.last_fix_utc = now
                                else:
                                    self.state.status = "lost" if self.state.last_fix_utc else "searching"

            except Exception as e:
                with self.lock:
                    # GPS 掛了也沒事：記錄 error，稍後重試
                    self.state.status = "error"
                    self.state.error = str(e)
                time.sleep(self.retry_sec)

def start_gps_reader(enabled: bool = True):
    state = GPSState(enabled=enabled, status="searching" if enabled else "disabled")
    lock = threading.Lock()
    GPSReader(state, lock).start()

    def snapshot():
        with lock:
            return asdict(state)

    return snapshot
