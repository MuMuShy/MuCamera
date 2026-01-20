#!/usr/bin/env python3
"""
MuMu Camera 錄影模組

使用 FFmpeg 從 go2rtc RTSP 串流錄製分段 .ts 檔案。
設計為非阻塞且容錯，支援自動重啟。
"""

import subprocess
import threading
import time
import os
import signal
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class RecorderManager:
    """
    FFmpeg 錄影管理器

    使用 ffmpeg segment muxer 錄製 RTSP 串流到分段 .ts 檔案。
    檔名格式：YYYYmmdd_HHMMSS.ts
    """

    def __init__(
        self,
        rtsp_url: str = "rtsp://127.0.0.1:8554/cam",
        output_dir: str = "/opt/mumucam/recordings/cam",
        segment_seconds: int = 300,
        enabled: bool = True
    ):
        self.rtsp_url = rtsp_url
        self.output_dir = Path(output_dir)
        self.segment_seconds = segment_seconds
        self.enabled = enabled

        self._process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # 統計資料
        self._start_time: Optional[datetime] = None
        self._restart_count = 0
        self._last_error: Optional[str] = None

    def start(self) -> bool:
        """啟動錄影"""
        if not self.enabled:
            logger.info("[recorder] 錄影已停用，不啟動")
            return False

        with self._lock:
            if self._process is not None and self._process.poll() is None:
                logger.warning("[recorder] 已在運行中")
                return False

            # 確保輸出目錄存在
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"[recorder] 輸出目錄：{self.output_dir}")
            except Exception as e:
                logger.error(f"[recorder] 建立輸出目錄失敗：{e}")
                self._last_error = str(e)
                return False

            # 啟動 ffmpeg 程序
            if not self._start_ffmpeg():
                return False

            # 啟動監控執行緒
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                daemon=True,
                name="RecorderMonitor"
            )
            self._monitor_thread.start()

            self._start_time = datetime.now()
            logger.info("[recorder] 已啟動")
            return True

    def stop(self) -> bool:
        """優雅停止錄影"""
        with self._lock:
            self._stop_event.set()

            if self._process is not None:
                logger.info("[recorder] 正在停止 ffmpeg...")
                try:
                    # 發送 SIGINT 以優雅關閉（完成當前分段）
                    self._process.send_signal(signal.SIGINT)
                    try:
                        self._process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        logger.warning("[recorder] ffmpeg 未能優雅停止，強制終止")
                        self._process.kill()
                        self._process.wait(timeout=5)
                except Exception as e:
                    logger.error(f"[recorder] 停止 ffmpeg 時發生錯誤：{e}")
                finally:
                    self._process = None

            if self._monitor_thread is not None:
                self._monitor_thread.join(timeout=2)
                self._monitor_thread = None

            self._start_time = None
            logger.info("[recorder] 已停止")
            return True

    def is_running(self) -> bool:
        """檢查錄影是否進行中"""
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def get_status(self) -> Dict[str, Any]:
        """取得錄影狀態"""
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            return {
                "enabled": self.enabled,
                "running": running,
                "rtsp_url": self.rtsp_url,
                "output_dir": str(self.output_dir),
                "segment_seconds": self.segment_seconds,
                "start_time": self._start_time.isoformat() if self._start_time else None,
                "restart_count": self._restart_count,
                "last_error": self._last_error
            }

    def _build_ffmpeg_command(self) -> list:
        """建構 ffmpeg 分段錄影命令"""
        output_pattern = str(self.output_dir / "%Y%m%d_%H%M%S.ts")

        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            # 輸入選項
            "-rtsp_transport", "tcp",
            "-i", self.rtsp_url,
            # 輸出選項 - 不轉碼
            "-c", "copy",
            # 分段 muxer
            "-f", "segment",
            "-segment_time", str(self.segment_seconds),
            "-segment_format", "mpegts",
            "-strftime", "1",
            "-reset_timestamps", "1",
            # 輸出格式
            output_pattern
        ]

    def _start_ffmpeg(self) -> bool:
        """啟動 ffmpeg 子程序"""
        cmd = self._build_ffmpeg_command()
        logger.info(f"[recorder] 啟動 ffmpeg：{' '.join(cmd)}")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # 建立新的程序群組以便信號處理
                start_new_session=True
            )
            self._last_error = None
            return True
        except FileNotFoundError:
            self._last_error = "找不到 ffmpeg"
            logger.error("[recorder] 找不到 ffmpeg，請確認已安裝")
            return False
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"[recorder] 啟動 ffmpeg 失敗：{e}")
            return False

    def _monitor_loop(self):
        """監控 ffmpeg 程序並在失敗時重啟"""
        logger.info("[recorder] 監控執行緒已啟動")
        restart_delay = 5  # 重啟間隔秒數

        while not self._stop_event.is_set():
            # 檢查程序狀態
            with self._lock:
                if self._process is None:
                    break

                retcode = self._process.poll()

                if retcode is not None:
                    # 程序已結束
                    stderr = ""
                    try:
                        _, stderr_bytes = self._process.communicate(timeout=1)
                        stderr = stderr_bytes.decode(errors="ignore")[-500:]  # 最後 500 字元
                    except Exception:
                        pass

                    if retcode == 0:
                        logger.info("[recorder] ffmpeg 正常結束")
                    else:
                        self._last_error = f"結束碼 {retcode}：{stderr}"
                        logger.warning(f"[recorder] ffmpeg 結束碼 {retcode}：{stderr}")

                    self._process = None

                    # 如果不是停止中，則重啟
                    if not self._stop_event.is_set():
                        logger.info(f"[recorder] {restart_delay} 秒後重啟...")
                        self._restart_count += 1

            # 等待後檢查或重啟
            if self._stop_event.wait(timeout=restart_delay):
                break

            # 如果程序已死亡且非停止中，嘗試重啟
            with self._lock:
                if self._process is None and not self._stop_event.is_set():
                    logger.info("[recorder] 嘗試重啟...")
                    self._start_ffmpeg()

        logger.info("[recorder] 監控執行緒已停止")


def create_recorder_manager(
    rtsp_url: str = None,
    output_dir: str = None,
    segment_seconds: int = None,
    enabled: bool = None
) -> RecorderManager:
    """
    從環境變數或參數建立錄影管理器

    環境變數：
        RECORDING_ENABLED: "true" 或 "false"（預設："true"）
        RECORDING_RTSP_URL: RTSP 來源 URL（預設："rtsp://127.0.0.1:8554/cam"）
        RECORDING_DIR: 輸出目錄（預設："/opt/mumucam/recordings/cam"）
        RECORDING_SEGMENT_SECONDS: 分段時長（預設：300）
    """
    if enabled is None:
        enabled = os.getenv("RECORDING_ENABLED", "true").lower() == "true"
    if rtsp_url is None:
        rtsp_url = os.getenv("RECORDING_RTSP_URL", "rtsp://127.0.0.1:8554/cam")
    if output_dir is None:
        output_dir = os.getenv("RECORDING_DIR", "/opt/mumucam/recordings/cam")
    if segment_seconds is None:
        segment_seconds = int(os.getenv("RECORDING_SEGMENT_SECONDS", "300"))

    return RecorderManager(
        rtsp_url=rtsp_url,
        output_dir=output_dir,
        segment_seconds=segment_seconds,
        enabled=enabled
    )


# 允許作為獨立程式執行測試
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    # 使用環境變數設定建立錄影器
    recorder = create_recorder_manager()

    print(f"錄影設定：{recorder.get_status()}")

    if not recorder.enabled:
        print("錄影已停用。設定 RECORDING_ENABLED=true 以啟用。")
        sys.exit(0)

    print("啟動錄影中... (Ctrl+C 停止)")
    recorder.start()

    try:
        while True:
            time.sleep(10)
            status = recorder.get_status()
            print(f"狀態：running={status['running']}, restarts={status['restart_count']}")
    except KeyboardInterrupt:
        print("\n停止中...")
        recorder.stop()
        print("完成。")
