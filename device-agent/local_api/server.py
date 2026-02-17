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
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response, PlainTextResponse
from pydantic import BaseModel

# 新增父目錄到路徑以便 import
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from indexer import create_indexer

logger = logging.getLogger(__name__)

# 從環境變數讀取設定
RECORDING_DIR = os.getenv("RECORDING_DIR", "/mnt/usb/recordings/cam")
RECORDING_DIR_CAM2 = os.getenv("RECORDING_DIR_CAM2", "/mnt/usb/recordings/cam2")
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

# 多攝影機索引器
_indexers = {}
_recording_dirs = {
    "cam": RECORDING_DIR,
    "cam2": RECORDING_DIR_CAM2,
}


def get_indexer(source: str = "cam"):
    """取得指定攝影機的索引器"""
    if source not in _recording_dirs:
        source = "cam"
    if source not in _indexers:
        rec_dir = _recording_dirs[source]
        db_path = os.path.join(rec_dir, "recordings.db")
        _indexers[source] = create_indexer(recording_dir=rec_dir, db_path=db_path)
    return _indexers[source]


def get_recording_dir(source: str = "cam") -> str:
    """取得指定攝影機的錄影目錄"""
    return _recording_dirs.get(source, RECORDING_DIR)


# 保持向後相容
indexer = None  # lazy init


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


class TimelineSegment(BaseModel):
    """時間軸上的錄影片段"""
    filename: str
    start_time: str  # ISO 格式
    end_time: str    # ISO 格式（估算）
    duration_seconds: int
    file_size_bytes: Optional[int]


class TimelineResponse(BaseModel):
    """時間軸回應"""
    date: str
    segments: List[TimelineSegment]
    total_duration_seconds: int


# 錄影分段時長（用於估算結束時間）
RECORDING_SEGMENT_SECONDS = int(os.getenv("RECORDING_SEGMENT_SECONDS", "300"))


@app.on_event("startup")
async def startup_event():
    """啟動時初始化"""
    # 確保 HLS 快取目錄存在
    Path(HLS_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    logger.info(f"[local_api] 已啟動於 {LOCAL_API_HOST}:{LOCAL_API_PORT}")
    logger.info(f"[local_api] 錄影目錄：{_recording_dirs}")
    logger.info(f"[local_api] HLS 快取目錄：{HLS_CACHE_DIR}")

    # 確保所有錄影目錄存在並初始掃描
    for source, rec_dir in _recording_dirs.items():
        Path(rec_dir).mkdir(parents=True, exist_ok=True)
        idx = get_indexer(source)
        idx.scan_directory()
        logger.info(f"[local_api] 已初始化索引器：{source} → {rec_dir}")


@app.get("/health")
async def health_check():
    """健康檢查端點"""
    return {"status": "ok", "recording_dir": RECORDING_DIR}


@app.get("/recordings", response_model=RecordingListResponse)
async def list_recordings(
    start_date: Optional[str] = Query(None, description="篩選開始日期（ISO 格式）"),
    end_date: Optional[str] = Query(None, description="篩選結束日期（ISO 格式）"),
    page: int = Query(1, ge=1, description="頁碼"),
    limit: int = Query(50, ge=1, le=100, description="每頁數量"),
    source: str = Query("cam", description="攝影機來源（cam 或 cam2）")
):
    """列出錄影，可選擇性地依時間篩選"""
    idx = get_indexer(source)

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
    idx.scan_directory()

    # 查詢錄影
    offset = (page - 1) * limit
    recordings = idx.get_recordings(
        start_date=start_dt,
        end_date=end_dt,
        limit=limit,
        offset=offset
    )

    stats = idx.get_stats()

    return RecordingListResponse(
        recordings=[RecordingResponse(**r) for r in recordings],
        total=stats["count"],
        page=page,
        limit=limit
    )


@app.get("/recordings/stats", response_model=StatsResponse)
async def get_stats(
    source: str = Query("cam", description="攝影機來源（cam 或 cam2）")
):
    """取得錄影統計"""
    idx = get_indexer(source)
    idx.scan_directory()
    stats = idx.get_stats()
    return StatsResponse(**stats)


@app.get("/recordings/timeline")
async def get_timeline(
    date: str = Query(..., description="日期（YYYY-MM-DD 格式）"),
    source: str = Query("cam", description="攝影機來源（cam 或 cam2）")
):
    """
    取得指定日期的錄影時間軸。

    回傳該日所有錄影片段的起止時間，供前端繪製時間軸。
    """
    idx = get_indexer(source)

    # 解析日期
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "日期格式無效，請使用 YYYY-MM-DD")

    # 設定查詢範圍（該日 00:00:00 到 23:59:59）
    start_dt = datetime.combine(target_date, datetime.min.time())
    end_dt = datetime.combine(target_date, datetime.max.time())

    # 掃描並查詢
    idx.scan_directory()
    recordings = idx.get_recordings(
        start_date=start_dt,
        end_date=end_dt,
        limit=1000,  # 一天最多 288 個 5 分鐘片段
        offset=0
    )

    # 轉換為時間軸格式
    segments = []
    total_duration = 0

    for rec in recordings:
        start_time = datetime.fromisoformat(rec["start_time"])

        # 估算結束時間（使用檔案實際時長或預設分段時長）
        duration = rec.get("duration_seconds") or RECORDING_SEGMENT_SECONDS
        end_time = start_time + timedelta(seconds=duration)

        segments.append(TimelineSegment(
            filename=rec["filename"],
            start_time=rec["start_time"],
            end_time=end_time.isoformat(),
            duration_seconds=duration,
            file_size_bytes=rec.get("file_size_bytes")
        ))
        total_duration += duration

    # 依時間排序（最早的在前）
    segments.sort(key=lambda x: x.start_time)

    return TimelineResponse(
        date=date,
        segments=segments,
        total_duration_seconds=total_duration
    )


@app.get("/recordings/stream")
async def get_stream_playlist(
    start: str = Query(..., description="開始時間（ISO 格式）"),
    end: Optional[str] = Query(None, description="結束時間（ISO 格式，可選）"),
    source: str = Query("cam", description="攝影機來源（cam 或 cam2）")
):
    """
    取得時間範圍的連續 HLS 播放清單。

    此端點會找出指定時間範圍內的所有錄影檔案，
    產生一個合併的 HLS 播放清單，實現跨檔連續播放。
    """
    idx = get_indexer(source)
    rec_dir = get_recording_dir(source)

    # 解析時間
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "start 時間格式無效")

    end_dt = None
    if end:
        try:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "end 時間格式無效")
    else:
        # 預設只載入起始時間後 30 分鐘（約 6 個 5 分鐘片段），避免一次轉換太多
        end_dt = start_dt + timedelta(minutes=30)

    # 查詢時間範圍內的錄影
    idx.scan_directory()

    # 擴大查詢範圍以確保涵蓋起始時間所在的檔案
    query_start = start_dt - timedelta(seconds=RECORDING_SEGMENT_SECONDS)

    recordings = idx.get_recordings(
        start_date=query_start,
        end_date=end_dt,
        limit=1000,
        offset=0
    )

    if not recordings:
        raise HTTPException(404, "指定時間範圍內沒有錄影")

    # 依時間排序（最早的在前）
    recordings.sort(key=lambda x: x["start_time"])

    # 篩選出包含指定開始時間的檔案及之後的檔案
    filtered_recordings = []
    for rec in recordings:
        rec_start = datetime.fromisoformat(rec["start_time"])
        rec_end = rec_start + timedelta(seconds=rec.get("duration_seconds") or RECORDING_SEGMENT_SECONDS)

        # 如果此錄影的時間範圍與請求的時間範圍重疊
        if rec_end > start_dt and rec_start < end_dt:
            filtered_recordings.append(rec)

    if not filtered_recordings:
        raise HTTPException(404, "指定時間範圍內沒有錄影")

    # 計算第一個檔案內的起始偏移量
    first_rec = filtered_recordings[0]
    first_rec_start = datetime.fromisoformat(first_rec["start_time"])
    start_offset_seconds = max(0, (start_dt - first_rec_start).total_seconds())

    # 產生合併的 HLS 播放清單
    # 使用時間範圍的 hash 作為快取 key
    cache_key = hashlib.md5(f"{start}_{end}".encode()).hexdigest()[:12]
    cache_dir = Path(HLS_CACHE_DIR) / f"stream_{cache_key}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    playlist_path = cache_dir / "playlist.m3u8"

    # 產生各檔案的 HLS 分段並合併
    playlist_content = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{HLS_SEGMENT_DURATION}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]

    segment_index = 0

    # 並行產生所有檔案的 HLS 分段
    async def ensure_hls(rec):
        """確保單一錄影檔的 HLS 已產生"""
        source_path = Path(rec_dir) / rec["filename"]
        if not source_path.exists():
            logger.warning(f"[local_api] 檔案不存在：{rec['filename']}")
            return False

        file_hash = hashlib.md5(rec["filename"].encode()).hexdigest()[:8]
        file_cache_dir = Path(HLS_CACHE_DIR) / file_hash
        file_cache_dir.mkdir(parents=True, exist_ok=True)
        file_playlist_path = file_cache_dir / "playlist.m3u8"

        if not file_playlist_path.exists() or source_path.stat().st_mtime > file_playlist_path.stat().st_mtime:
            logger.info(f"[local_api] 產生 HLS：{rec['filename']}")

            for old_file in file_cache_dir.glob("*.ts"):
                old_file.unlink()

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "warning",
                "-i", str(source_path),
                "-c", "copy",
                "-f", "hls",
                "-hls_time", str(HLS_SEGMENT_DURATION),
                "-hls_list_size", "0",
                "-hls_segment_filename", str(file_cache_dir / "segment_%03d.ts"),
                str(file_playlist_path)
            ]

            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await asyncio.wait_for(process.communicate(), timeout=120)

                if process.returncode != 0:
                    logger.error(f"[local_api] ffmpeg 失敗：{rec['filename']}")
                    return False
            except Exception as e:
                logger.error(f"[local_api] HLS 產生失敗：{e}")
                return False

        return True

    # 最多同時 3 個 ffmpeg（避免 Pi 記憶體不足）
    sem = asyncio.Semaphore(3)

    async def ensure_hls_limited(rec):
        async with sem:
            return await ensure_hls(rec)

    await asyncio.gather(*[ensure_hls_limited(rec) for rec in filtered_recordings])

    for i, rec in enumerate(filtered_recordings):
        file_hash = hashlib.md5(rec["filename"].encode()).hexdigest()[:8]
        file_cache_dir = Path(HLS_CACHE_DIR) / file_hash
        file_playlist_path = file_cache_dir / "playlist.m3u8"

        if not file_playlist_path.exists():
            continue

        # 讀取該檔案的播放清單並合併
        try:
            with open(file_playlist_path, "r") as f:
                file_playlist = f.read()

            # 解析並加入分段
            lines = file_playlist.strip().split("\n")
            skip_until_segment = 0

            # 如果是第一個檔案且有起始偏移，跳過前面的分段
            if i == 0 and start_offset_seconds > 0:
                skip_until_segment = int(start_offset_seconds // HLS_SEGMENT_DURATION)

            current_segment = 0
            if i > 0:
                # 在不同檔案之間加入 discontinuity 標記
                playlist_content.append("#EXT-X-DISCONTINUITY")

            for line in lines:
                if line.startswith("#EXTINF:"):
                    if current_segment >= skip_until_segment:
                        playlist_content.append(line)
                    current_segment += 1
                elif line.endswith(".ts") and current_segment > skip_until_segment:
                    # 使用絕對路徑指向分段
                    segment_name = line.strip()
                    # 建立符號連結或複製到合併目錄
                    src_segment = file_cache_dir / segment_name
                    dst_segment_name = f"seg_{segment_index:04d}.ts"
                    dst_segment = cache_dir / dst_segment_name

                    if src_segment.exists() and not dst_segment.exists():
                        # 建立硬連結以節省空間
                        try:
                            os.link(str(src_segment), str(dst_segment))
                        except OSError:
                            # 如果硬連結失敗，使用符號連結
                            dst_segment.symlink_to(src_segment)

                    playlist_content.append(dst_segment_name)
                    segment_index += 1

        except Exception as e:
            logger.error(f"[local_api] 讀取播放清單失敗：{e}")
            continue

    # 加入結束標記
    playlist_content.append("#EXT-X-ENDLIST")

    # 寫入合併的播放清單
    final_playlist = "\n".join(playlist_content)
    with open(playlist_path, "w") as f:
        f.write(final_playlist)

    logger.info(f"[local_api] 合併播放清單已產生：{len(filtered_recordings)} 個檔案，{segment_index} 個分段")

    return PlainTextResponse(
        content=final_playlist,
        media_type="application/vnd.apple.mpegurl"
    )


@app.get("/recordings/stream/{segment}")
async def get_stream_segment(
    segment: str,
    start: str = Query(..., description="原始請求的開始時間")
):
    """取得合併串流的分段檔案"""
    if not segment.endswith(".ts") or "/" in segment or "\\" in segment or ".." in segment:
        raise HTTPException(400, "分段名稱無效")

    # 使用相同的快取 key
    cache_key = hashlib.md5(f"{start}_None".encode()).hexdigest()[:12]
    segment_path = Path(HLS_CACHE_DIR) / f"stream_{cache_key}" / segment

    if not segment_path.exists():
        raise HTTPException(404, "找不到分段")

    return FileResponse(
        path=str(segment_path),
        media_type="video/mp2t"
    )


@app.get("/recordings/{filename}")
async def get_recording_info(
    filename: str,
    source: str = Query("cam", description="攝影機來源")
):
    """取得特定錄影的資訊"""
    idx = get_indexer(source)
    recording = idx.get_recording(filename)
    if not recording:
        raise HTTPException(404, "找不到錄影")
    return recording


@app.get("/recordings/{filename}/download")
async def download_recording(
    filename: str,
    source: str = Query("cam", description="攝影機來源"),
    format: str = Query("ts", description="下載格式（ts 或 mp4）")
):
    """直接下載錄影檔案（支援 MP4 格式轉換）"""
    # 驗證檔名（防止路徑穿越）
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "檔名無效")

    file_path = Path(get_recording_dir(source)) / filename
    if not file_path.exists():
        raise HTTPException(404, "找不到錄影檔案")

    if format == "mp4":
        # 用 ffmpeg remux .ts → .mp4（僅複製不重新編碼，速度很快）
        mp4_name = filename.replace(".ts", ".mp4")
        mp4_cache = Path(HLS_CACHE_DIR) / "mp4"
        mp4_cache.mkdir(parents=True, exist_ok=True)
        mp4_path = mp4_cache / mp4_name

        # 如果尚未轉換或來源更新，重新轉換
        if not mp4_path.exists() or file_path.stat().st_mtime > mp4_path.stat().st_mtime:
            cmd = [
                "ffmpeg", "-y",
                "-hide_banner", "-loglevel", "warning",
                "-i", str(file_path),
                "-c", "copy",
                "-movflags", "+faststart",
                str(mp4_path)
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
                if process.returncode != 0:
                    logger.error(f"[local_api] MP4 轉換失敗：{stderr.decode()}")
                    raise HTTPException(500, "MP4 轉換失敗")
            except asyncio.TimeoutError:
                raise HTTPException(500, "MP4 轉換逾時")

        return FileResponse(
            path=str(mp4_path),
            filename=mp4_name,
            media_type="video/mp4"
        )

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="video/mp2t"
    )


@app.get("/recordings/{filename}/hls/playlist.m3u8")
async def get_hls_playlist(
    filename: str,
    source: str = Query("cam", description="攝影機來源")
):
    """
    取得錄影的 HLS 播放清單。

    使用 ffmpeg 依需求產生 HLS 分段。
    """
    # 驗證檔名
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "檔名無效")

    source_path = Path(get_recording_dir(source)) / filename
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
async def trigger_scan(
    source: str = Query("cam", description="攝影機來源")
):
    """手動觸發目錄掃描"""
    idx = get_indexer(source)
    result = idx.scan_directory()
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
