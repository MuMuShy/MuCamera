#!/usr/bin/env python3
"""
GPS Reader Module for MuMu Camera Device Agent

Reads GPS data from serial device (e.g., SIM7600) in background.
Designed to be non-blocking and fault-tolerant - GPS issues won't affect main program.
"""

import threading
import time
import os
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Callable

logger = logging.getLogger(__name__)

try:
    import serial  # pyserial
except ImportError:
    serial = None
    logger.warning("[gps] pyserial not installed - GPS will be disabled")


@dataclass
class GPSState:
    """GPS state container"""
    enabled: bool = True
    status: str = "disabled"  # disabled/searching/fixed/lost/error
    lat: Optional[float] = None
    lon: Optional[float] = None
    satellites: Optional[int] = None
    hdop: Optional[float] = None
    speed_knots: Optional[float] = None
    course: Optional[float] = None
    last_fix_utc: Optional[str] = None
    last_sentence_utc: Optional[str] = None
    error: Optional[str] = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nmea_to_decimal(ddmm: str, hemi: str) -> Optional[float]:
    """Convert NMEA ddmm.mmmm format to decimal degrees"""
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
        return round(val, 6)
    except Exception:
        return None


def _parse_gga(line: str) -> Optional[dict]:
    """Parse GPGGA sentence"""
    p = line.split(",")
    if len(p) < 10:
        return None
    try:
        return {
            "fix": p[6],
            "lat": _nmea_to_decimal(p[2], p[3]),
            "lon": _nmea_to_decimal(p[4], p[5]),
            "sats": int(p[7]) if p[7].isdigit() else None,
            "hdop": float(p[8]) if p[8] else None,
        }
    except Exception:
        return None


def _parse_rmc(line: str) -> Optional[dict]:
    """Parse GPRMC sentence"""
    p = line.split(",")
    if len(p) < 8:
        return None
    try:
        speed = None
        course = None
        if len(p) > 7 and p[7]:
            try:
                speed = float(p[7])
            except ValueError:
                pass
        if len(p) > 8 and p[8]:
            try:
                course = float(p[8])
            except ValueError:
                pass
        return {
            "status": p[2],  # A=valid, V=invalid
            "lat": _nmea_to_decimal(p[3], p[4]),
            "lon": _nmea_to_decimal(p[5], p[6]),
            "speed_knots": speed,
            "course": course,
        }
    except Exception:
        return None


class GPSReader(threading.Thread):
    """Background GPS reader thread"""

    def __init__(
        self,
        state: GPSState,
        lock: threading.Lock,
        device: str = "/dev/sim7600-gps",
        baudrate: int = 115200,
        retry_sec: float = 5.0
    ):
        super().__init__(daemon=True, name="GPSReader")
        self.state = state
        self.lock = lock
        self.device = device
        self.baudrate = baudrate
        self.retry_sec = retry_sec
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        logger.info(f"[gps] Reader thread started (device={self.device})")

        while not self._stop_event.is_set():
            # Check if GPS is enabled
            if not self.state.enabled:
                with self.lock:
                    self.state.status = "disabled"
                    self.state.error = None
                time.sleep(5)
                continue

            # Check if pyserial is available
            if serial is None:
                with self.lock:
                    self.state.status = "disabled"
                    self.state.error = "pyserial not installed"
                time.sleep(10)
                continue

            # Try to open serial port and read GPS data
            try:
                with serial.Serial(self.device, self.baudrate, timeout=1) as ser:
                    logger.info(f"[gps] Connected to {self.device}")
                    with self.lock:
                        if self.state.status in ("disabled", "error"):
                            self.state.status = "searching"
                            self.state.error = None

                    while not self._stop_event.is_set():
                        try:
                            raw = ser.readline()
                        except Exception as e:
                            logger.warning(f"[gps] Read error: {e}")
                            break

                        if not raw:
                            continue

                        line = raw.decode(errors="ignore").strip()
                        if not line.startswith("$"):
                            continue

                        now = _utc_now()
                        with self.lock:
                            self.state.last_sentence_utc = now

                        # Parse GGA (position + satellites)
                        if line.startswith("$GPGGA") or line.startswith("$GNGGA"):
                            g = _parse_gga(line)
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

                        # Parse RMC (position + speed + course)
                        elif line.startswith("$GPRMC") or line.startswith("$GNRMC"):
                            r = _parse_rmc(line)
                            if not r:
                                continue
                            with self.lock:
                                if r["status"] == "A" and r["lat"] is not None and r["lon"] is not None:
                                    self.state.status = "fixed"
                                    self.state.lat = r["lat"]
                                    self.state.lon = r["lon"]
                                    self.state.speed_knots = r["speed_knots"]
                                    self.state.course = r["course"]
                                    self.state.last_fix_utc = now
                                else:
                                    self.state.status = "lost" if self.state.last_fix_utc else "searching"

            except FileNotFoundError:
                with self.lock:
                    self.state.status = "error"
                    self.state.error = f"Device not found: {self.device}"
                logger.debug(f"[gps] Device not found: {self.device}")
            except Exception as e:
                with self.lock:
                    self.state.status = "error"
                    self.state.error = str(e)
                logger.warning(f"[gps] Error: {e}")

            # Wait before retry
            if not self._stop_event.is_set():
                time.sleep(self.retry_sec)

        logger.info("[gps] Reader thread stopped")


class GPSManager:
    """GPS Manager - provides thread-safe access to GPS data"""

    def __init__(
        self,
        enabled: bool = True,
        device: str = "/dev/sim7600-gps",
        baudrate: int = 115200
    ):
        self.enabled = enabled
        self.device = device
        self.baudrate = baudrate

        self._state = GPSState(
            enabled=enabled,
            status="searching" if enabled else "disabled"
        )
        self._lock = threading.Lock()
        self._reader: Optional[GPSReader] = None

    def start(self):
        """Start GPS reader thread"""
        if not self.enabled:
            logger.info("[gps] GPS disabled, not starting reader")
            return

        if self._reader is not None and self._reader.is_alive():
            logger.warning("[gps] Reader already running")
            return

        self._reader = GPSReader(
            state=self._state,
            lock=self._lock,
            device=self.device,
            baudrate=self.baudrate
        )
        self._reader.start()
        logger.info("[gps] Manager started")

    def stop(self):
        """Stop GPS reader thread"""
        if self._reader is not None:
            self._reader.stop()
            self._reader.join(timeout=2)
            self._reader = None
        logger.info("[gps] Manager stopped")

    def get_snapshot(self) -> dict:
        """Get current GPS state as dict (thread-safe)"""
        with self._lock:
            return asdict(self._state)

    def is_fixed(self) -> bool:
        """Check if GPS has a fix"""
        with self._lock:
            return self._state.status == "fixed"

    def get_position(self) -> Optional[tuple]:
        """Get current position as (lat, lon) or None"""
        with self._lock:
            if self._state.status == "fixed" and self._state.lat and self._state.lon:
                return (self._state.lat, self._state.lon)
            return None


def create_gps_manager(
    enabled: bool = None,
    device: str = None,
    baudrate: int = None
) -> GPSManager:
    """
    Create GPS manager from environment variables or parameters

    Environment variables:
        GPS_ENABLED: "true" or "false" (default: "false")
        GPS_DEVICE: Serial device path (default: "/dev/sim7600-gps")
        GPS_BAUDRATE: Baud rate (default: 115200)
    """
    if enabled is None:
        enabled = os.getenv("GPS_ENABLED", "false").lower() == "true"
    if device is None:
        device = os.getenv("GPS_DEVICE", "/dev/sim7600-gps")
    if baudrate is None:
        baudrate = int(os.getenv("GPS_BAUDRATE", "115200"))

    return GPSManager(enabled=enabled, device=device, baudrate=baudrate)
