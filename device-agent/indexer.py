#!/usr/bin/env python3
"""
MuMu Camera 錄影索引模組

維護錄影檔案的 SQLite 資料庫索引。
支援依時間範圍查詢錄影以供回放 API 使用。
"""

import os
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class RecordingIndexer:
    """
    基於 SQLite 的錄影檔案索引器。

    維護錄影檔案的索引，包含時間戳和中繼資料。
    """

    def __init__(
        self,
        recording_dir: str = "/opt/mumucam/recordings/cam",
        db_path: str = None
    ):
        self.recording_dir = Path(recording_dir)

        # 預設資料庫路徑在錄影目錄的上層
        if db_path is None:
            db_path = str(self.recording_dir.parent / "recordings.db")

        self.db_path = db_path
        self._init_database()

    def _init_database(self):
        """初始化資料庫架構"""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recordings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT UNIQUE NOT NULL,
                    start_time TEXT NOT NULL,
                    duration_seconds INTEGER,
                    file_size_bytes INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_start_time
                ON recordings(start_time)
            """)
            conn.commit()
        logger.info(f"[indexer] 資料庫已初始化：{self.db_path}")

    @contextmanager
    def _get_connection(self):
        """取得資料庫連線（含 context manager）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _parse_filename(self, filename: str) -> Optional[datetime]:
        """從檔名解析時間戳（YYYYmmdd_HHMMSS.ts）"""
        try:
            stem = Path(filename).stem
            return datetime.strptime(stem, "%Y%m%d_%H%M%S")
        except ValueError:
            return None

    def scan_directory(self) -> Dict[str, int]:
        """
        掃描錄影目錄並更新索引。

        回傳計數字典：{"added": N, "removed": N, "total": N}
        """
        if not self.recording_dir.exists():
            logger.warning(f"[indexer] 錄影目錄不存在：{self.recording_dir}")
            return {"added": 0, "removed": 0, "total": 0}

        # 取得磁碟上的檔案
        disk_files = {}
        for f in self.recording_dir.iterdir():
            if f.is_file() and f.suffix == ".ts":
                disk_files[f.name] = f

        # 取得資料庫中已索引的檔案
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT filename FROM recordings")
            db_files = set(row["filename"] for row in cursor.fetchall())

        # 找出要新增的檔案
        new_files = set(disk_files.keys()) - db_files
        added = 0

        for filename in new_files:
            file_path = disk_files[filename]
            start_time = self._parse_filename(filename)

            if start_time is None:
                logger.warning(f"[indexer] 無法從 {filename} 解析時間戳")
                continue

            try:
                file_size = file_path.stat().st_size
                self._add_recording(filename, start_time, file_size)
                added += 1
            except Exception as e:
                logger.error(f"[indexer] 新增 {filename} 時發生錯誤：{e}")

        # 找出要移除的檔案
        removed_files = db_files - set(disk_files.keys())
        removed = 0

        for filename in removed_files:
            try:
                self._remove_recording(filename)
                removed += 1
            except Exception as e:
                logger.error(f"[indexer] 移除 {filename} 時發生錯誤：{e}")

        total = len(db_files) + added - removed

        if added > 0 or removed > 0:
            logger.info(f"[indexer] 掃描完成：+{added} -{removed} = {total} 總計")

        return {"added": added, "removed": removed, "total": total}

    def _add_recording(self, filename: str, start_time: datetime, file_size: int):
        """新增錄影到索引"""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO recordings
                (filename, start_time, file_size_bytes)
                VALUES (?, ?, ?)
                """,
                (filename, start_time.isoformat(), file_size)
            )
            conn.commit()
        logger.debug(f"[indexer] 已新增：{filename}")

    def _remove_recording(self, filename: str):
        """從索引移除錄影"""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM recordings WHERE filename = ?", (filename,))
            conn.commit()
        logger.debug(f"[indexer] 已移除：{filename}")

    def get_recordings(
        self,
        start_date: datetime = None,
        end_date: datetime = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        查詢錄影，可選擇性地依時間範圍篩選。

        參數：
            start_date: 篩選從此時間開始的錄影
            end_date: 篩選在此時間之前的錄影
            limit: 最大結果數
            offset: 略過前 N 筆結果

        回傳：
            依 start_time 降序排列的錄影字典列表（最新的在前）
        """
        query = "SELECT * FROM recordings WHERE 1=1"
        params = []

        if start_date:
            query += " AND start_time >= ?"
            params.append(start_date.isoformat())

        if end_date:
            query += " AND start_time <= ?"
            params.append(end_date.isoformat())

        query += " ORDER BY start_time DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        return [dict(row) for row in rows]

    def get_recording(self, filename: str) -> Optional[Dict[str, Any]]:
        """依檔名取得單一錄影"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM recordings WHERE filename = ?",
                (filename,)
            )
            row = cursor.fetchone()

        if row:
            return dict(row)
        return None

    def get_stats(self) -> Dict[str, Any]:
        """取得索引統計資料"""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as count,
                    COALESCE(SUM(file_size_bytes), 0) as total_size,
                    MIN(start_time) as oldest,
                    MAX(start_time) as newest
                FROM recordings
            """)
            row = cursor.fetchone()

        return {
            "count": row["count"],
            "total_size_bytes": row["total_size"],
            "total_size_gib": round(row["total_size"] / (1024**3), 2) if row["total_size"] else 0,
            "oldest": row["oldest"],
            "newest": row["newest"]
        }

    def update_duration(self, filename: str, duration_seconds: int):
        """更新錄影時長（若稍後由 ffprobe 取得）"""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE recordings SET duration_seconds = ? WHERE filename = ?",
                (duration_seconds, filename)
            )
            conn.commit()


def create_indexer(
    recording_dir: str = None,
    db_path: str = None
) -> RecordingIndexer:
    """
    從環境變數或參數建立索引器。

    環境變數：
        RECORDING_DIR: 錄影目錄（預設：/opt/mumucam/recordings/cam）
        RECORDING_DB_PATH: 資料庫路徑（預設：/opt/mumucam/recordings/recordings.db）
    """
    if recording_dir is None:
        recording_dir = os.getenv("RECORDING_DIR", "/opt/mumucam/recordings/cam")
    if db_path is None:
        db_path = os.getenv("RECORDING_DB_PATH")

    return RecordingIndexer(recording_dir=recording_dir, db_path=db_path)


# 允許作為獨立程式執行測試/手動同步
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    parser = argparse.ArgumentParser(description="MuMuCam 錄影索引器")
    parser.add_argument("--scan", action="store_true", help="掃描目錄並更新索引")
    parser.add_argument("--list", action="store_true", help="列出最近的錄影")
    parser.add_argument("--stats", action="store_true", help="顯示索引統計")
    parser.add_argument("--verbose", "-v", action="store_true", help="啟用除錯日誌")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    indexer = create_indexer()

    if args.scan or not (args.list or args.stats):
        print("掃描目錄中...")
        result = indexer.scan_directory()
        print(f"新增：{result['added']}，移除：{result['removed']}，總計：{result['total']}")

    if args.stats:
        print("\n--- 索引統計 ---")
        stats = indexer.get_stats()
        print(f"錄影數量：{stats['count']}")
        print(f"總大小：{stats['total_size_gib']:.2f} GiB")
        print(f"最舊：{stats['oldest']}")
        print(f"最新：{stats['newest']}")

    if args.list:
        print("\n--- 最近錄影 ---")
        recordings = indexer.get_recordings(limit=10)
        for rec in recordings:
            size_mb = rec['file_size_bytes'] / (1024**2) if rec['file_size_bytes'] else 0
            print(f"  {rec['filename']} - {rec['start_time']}（{size_mb:.1f} MiB）")
