#!/usr/bin/env python3
"""
MuMu Camera 本地回放 API

FastAPI 伺服器提供：
- 錄影列表 API
- HLS 串流（依需求轉換）
- 直接檔案下載

僅在 localhost 運行，透過主後端代理存取。
"""

import os
import asyncio
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

# 新增父目錄到路徑以便 import
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from indexer import create_indexer

logger = logging.getLogger(__name__)

# 從環境變數讀取設定
RECORDING_DIR = os.getenv("RECORDING_DIR", "/opt/mumucam/recordings/cam")
HLS_CACHE_DIR = os.getenv("HLS_CACHE_DIR", "/tmp/mumucam-hls")
HLS_SEGMENT_DURATION = int(os.getenv("HLS_SEGMENT_DURATION", "10"))
LOCAL_API_HOST = os.getenv("LOCAL_API_HOST", "127.0.0.1")
LOCAL_API_PORT = int(os.getenv("LOCAL_API_PORT", "8090"))

# 初始化 FastAPI
app = FastAPI(
    title="MuMuCam 本地回放 API",
    description="提供錄影回放的本地 API",
    version="1.0.0"
)

# 初始化索引器
indexer = create_indexer(recording_dir=RECORDING_DIR)


class RecordingResponse(BaseModel):
    filename: str
    start_time: str
    duration_seconds: Optional[int]
    file_size_bytes: Optional[int]


class RecordingListResponse(BaseModel):
    recordings: list[RecordingResponse]
    total: int
    page: int
    limit: int


class StatsResponse(BaseModel):
    count: int
    total_size_bytes: int
    total_size_gib: float
    oldest: Optional[str]
    newest: Optional[str]


@app.on_event("startup")
async def startup_event():
    """啟動時初始化"""
    # 確保 HLS 快取目錄存在
    Path(HLS_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    logger.info(f"[local_api] 已啟動於 {LOCAL_API_HOST}:{LOCAL_API_PORT}")
    logger.info(f"[local_api] 錄影目錄：{RECORDING_DIR}")
    logger.info(f"[local_api] HLS 快取目錄：{HLS_CACHE_DIR}")

    # 初始掃描
    indexer.scan_directory()


@app.get("/health")
async def health_check():
    """健康檢查端點"""
    return {"status": "ok", "recording_dir": RECORDING_DIR}


@app.get("/recordings", response_model=RecordingListResponse)
async def list_recordings(
    start_date: Optional[str] = Query(None, description="篩選開始日期（ISO 格式）"),
    end_date: Optional[str] = Query(None, description="篩選結束日期（ISO 格式）"),
    page: int = Query(1, ge=1, description="頁碼"),
    limit: int = Query(50, ge=1, le=100, description="每頁數量")
):
    """列出錄影，可選擇性地依時間篩選"""
    # 解析日期
    start_dt = None
    end_dt = None

    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "start_date 格式無效")

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "end_date 格式無效")

    # 掃描目錄以更新
    indexer.scan_directory()

    # 查詢錄影
    offset = (page - 1) * limit
    recordings = indexer.get_recordings(
        start_date=start_dt,
        end_date=end_dt,
        limit=limit,
        offset=offset
    )

    stats = indexer.get_stats()

    return RecordingListResponse(
        recordings=[RecordingResponse(**r) for r in recordings],
        total=stats["count"],
        page=page,
        limit=limit
    )


@app.get("/recordings/stats", response_model=StatsResponse)
async def get_stats():
    """取得錄影統計"""
    indexer.scan_directory()
    stats = indexer.get_stats()
    return StatsResponse(**stats)


@app.get("/recordings/{filename}")
async def get_recording_info(filename: str):
    """取得特定錄影的資訊"""
    recording = indexer.get_recording(filename)
    if not recording:
        raise HTTPException(404, "找不到錄影")
    return recording


@app.get("/recordings/{filename}/download")
async def download_recording(filename: str):
    """直接下載錄影檔案"""
    # 驗證檔名（防止路徑穿越）
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "檔名無效")

    file_path = Path(RECORDING_DIR) / filename
    if not file_path.exists():
        raise HTTPException(404, "找不到錄影檔案")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="video/mp2t"
    )


@app.get("/recordings/{filename}/hls/playlist.m3u8")
async def get_hls_playlist(filename: str):
    """
    取得錄影的 HLS 播放清單。

    使用 ffmpeg 依需求產生 HLS 分段。
    """
    # 驗證檔名
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "檔名無效")

    source_path = Path(RECORDING_DIR) / filename
    if not source_path.exists():
        raise HTTPException(404, "找不到錄影檔案")

    # 為此檔案建立快取目錄
    file_hash = hashlib.md5(filename.encode()).hexdigest()[:8]
    cache_dir = Path(HLS_CACHE_DIR) / file_hash
    cache_dir.mkdir(parents=True, exist_ok=True)

    playlist_path = cache_dir / "playlist.m3u8"

    # 若未快取或來源較新，則產生 HLS
    if not playlist_path.exists() or source_path.stat().st_mtime > playlist_path.stat().st_mtime:
        logger.info(f"[local_api] 產生 HLS：{filename}")

        # 清除舊分段
        for old_file in cache_dir.glob("*.ts"):
            old_file.unlink()
        if playlist_path.exists():
            playlist_path.unlink()

        # 使用 ffmpeg 產生 HLS
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-i", str(source_path),
            "-c", "copy",
            "-f", "hls",
            "-hls_time", str(HLS_SEGMENT_DURATION),
            "-hls_list_size", "0",
            "-hls_segment_filename", str(cache_dir / "segment_%03d.ts"),
            str(playlist_path)
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=60  # 60 秒逾時
            )

            if process.returncode != 0:
                logger.error(f"[local_api] ffmpeg 失敗：{stderr.decode()}")
                raise HTTPException(500, "產生 HLS 播放清單失敗")

            logger.info(f"[local_api] HLS 已產生：{filename}")

        except asyncio.TimeoutError:
            logger.error(f"[local_api] ffmpeg 逾時：{filename}")
            raise HTTPException(500, "HLS 產生逾時")
        except FileNotFoundError:
            raise HTTPException(500, "找不到 ffmpeg")

    # 回傳播放清單
    return FileResponse(
        path=str(playlist_path),
        media_type="application/vnd.apple.mpegurl"
    )


@app.get("/recordings/{filename}/hls/{segment}")
async def get_hls_segment(filename: str, segment: str):
    """取得 HLS 分段檔案"""
    # 驗證輸入
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "檔名無效")
    if not segment.endswith(".ts") or "/" in segment or "\\" in segment or ".." in segment:
        raise HTTPException(400, "分段名稱無效")

    file_hash = hashlib.md5(filename.encode()).hexdigest()[:8]
    segment_path = Path(HLS_CACHE_DIR) / file_hash / segment

    if not segment_path.exists():
        raise HTTPException(404, "找不到分段")

    return FileResponse(
        path=str(segment_path),
        media_type="video/mp2t"
    )


@app.post("/scan")
async def trigger_scan():
    """手動觸發目錄掃描"""
    result = indexer.scan_directory()
    return result


# 直接執行時使用 uvicorn
if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    uvicorn.run(
        "server:app",
        host=LOCAL_API_HOST,
        port=LOCAL_API_PORT,
        reload=False
    )
