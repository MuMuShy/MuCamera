#!/usr/bin/env python3
"""
MuMu Camera 錄影清理模組

實作雙條件清理策略：
- 條件 A（優先）：容量超過上限 → 刪除最舊檔案直到目標容量
- 條件 B（次要）：容量未超過 → 刪除超過保留天數的檔案

設計為安全、冪等，適合長時間無人值守運作。
"""

import os
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CleanupStats:
    """清理統計資料"""
    total_files: int = 0
    total_size_bytes: int = 0
    deleted_files: int = 0
    deleted_size_bytes: int = 0
    reason: str = ""  # "capacity" 或 "retention" 或 "none"


def get_dir_size_bytes(path: Path) -> int:
    """計算目錄中所有檔案的總大小（不遞迴，僅錄影檔）"""
    total = 0
    try:
        for f in path.iterdir():
            if f.is_file() and f.suffix == ".ts":
                total += f.stat().st_size
    except Exception as e:
        logger.warning(f"[cleaner] 計算目錄大小時發生錯誤：{e}")
    return total


def get_recording_files(path: Path) -> List[Tuple[Path, datetime, int]]:
    """
    取得錄影檔案列表，依時間戳排序（最舊的在前）。

    回傳 (路徑, 時間戳, 大小) 元組的列表。
    從檔名格式 YYYYmmdd_HHMMSS.ts 解析時間戳。
    """
    files = []

    try:
        for f in path.iterdir():
            if not f.is_file() or f.suffix != ".ts":
                continue

            # 從檔名解析時間戳
            try:
                # 格式：20250121_143000.ts
                stem = f.stem  # 20250121_143000
                ts = datetime.strptime(stem, "%Y%m%d_%H%M%S")
            except ValueError:
                # 如果檔名不符合預期格式，使用檔案修改時間
                logger.debug(f"[cleaner] 無法從 {f.name} 解析時間戳，使用修改時間")
                ts = datetime.fromtimestamp(f.stat().st_mtime)

            size = f.stat().st_size
            files.append((f, ts, size))
    except Exception as e:
        logger.error(f"[cleaner] 列出檔案時發生錯誤：{e}")
        return []

    # 依時間戳排序，最舊的在前
    files.sort(key=lambda x: x[1])
    return files


def cleanup_recordings(
    recording_dir: str = "/opt/mumucam/recordings/cam",
    max_gib: float = 50.0,
    target_gib: float = 40.0,
    retention_days: int = 7,
    dry_run: bool = False
) -> CleanupStats:
    """
    對錄影目錄執行雙條件清理。

    參數：
        recording_dir: 錄影目錄路徑
        max_gib: 容量上限（GiB），超過時觸發清理
        target_gib: 目標容量（GiB），清理到此為止
        retention_days: 保留天數（容量未超過時刪除過期檔案）
        dry_run: 若為 True，僅報告不實際刪除

    回傳：
        CleanupStats 包含清理操作的詳細資訊
    """
    stats = CleanupStats()
    path = Path(recording_dir)

    if not path.exists():
        logger.info(f"[cleaner] 錄影目錄不存在：{path}")
        return stats

    # 取得所有錄影檔案
    files = get_recording_files(path)
    if not files:
        logger.info("[cleaner] 沒有找到錄影檔案")
        return stats

    stats.total_files = len(files)
    stats.total_size_bytes = sum(f[2] for f in files)

    current_size_gib = stats.total_size_bytes / (1024**3)
    logger.info(f"[cleaner] 找到 {stats.total_files} 個檔案，總大小：{current_size_gib:.2f} GiB")

    # 轉換容量限制為位元組
    max_bytes = max_gib * (1024**3)
    target_bytes = target_gib * (1024**3)

    files_to_delete: List[Tuple[Path, str]] = []  # (路徑, 原因)

    # 條件 A：容量超過 - 刪除最舊的直到目標容量
    if stats.total_size_bytes > max_bytes:
        logger.info(f"[cleaner] 容量超過限制（{current_size_gib:.2f} > {max_gib} GiB），依容量清理...")
        stats.reason = "capacity"

        current_size = stats.total_size_bytes
        for file_path, file_ts, file_size in files:
            if current_size <= target_bytes:
                break

            files_to_delete.append((file_path, "capacity"))
            current_size -= file_size
            logger.debug(f"[cleaner] 標記刪除（容量）：{file_path.name}")

    # 條件 B：容量未超過 - 刪除超過保留天數的檔案
    else:
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        logger.info(f"[cleaner] 檢查保留期限（早於 {cutoff_date.date()} 的檔案）...")

        for file_path, file_ts, file_size in files:
            if file_ts < cutoff_date:
                files_to_delete.append((file_path, "retention"))
                logger.debug(f"[cleaner] 標記刪除（過期）：{file_path.name}（{file_ts}）")

        if files_to_delete:
            stats.reason = "retention"
        else:
            stats.reason = "none"

    # 刪除標記的檔案
    if not files_to_delete:
        logger.info("[cleaner] 沒有需要刪除的檔案")
        return stats

    logger.info(f"[cleaner] {'將刪除' if dry_run else '正在刪除'} {len(files_to_delete)} 個檔案...")

    for file_path, reason in files_to_delete:
        try:
            file_size = file_path.stat().st_size

            if not dry_run:
                file_path.unlink()
                logger.info(f"[cleaner] 已刪除：{file_path.name}（{file_size / (1024**2):.1f} MiB）[{reason}]")
            else:
                logger.info(f"[cleaner] 將刪除：{file_path.name}（{file_size / (1024**2):.1f} MiB）[{reason}]")

            stats.deleted_files += 1
            stats.deleted_size_bytes += file_size

        except Exception as e:
            logger.error(f"[cleaner] 刪除 {file_path} 失敗：{e}")

    deleted_gib = stats.deleted_size_bytes / (1024**3)
    logger.info(f"[cleaner] {'將釋放' if dry_run else '已釋放'}：{deleted_gib:.2f} GiB")

    return stats


def cleanup_multi_recordings(
    recording_dirs: List[str],
    max_gib: float = 50.0,
    target_gib: float = 40.0,
    retention_days: int = 7,
    dry_run: bool = False
) -> CleanupStats:
    """
    跨多目錄統一清理。將所有目錄的檔案混合排序，
    按全域容量上限刪除最舊檔案。

    參數：
        recording_dirs: 多個錄影目錄路徑
        max_gib: 所有目錄合計容量上限（GiB）
        target_gib: 目標容量（GiB）
        retention_days: 保留天數
        dry_run: 若為 True，僅報告不實際刪除
    """
    stats = CleanupStats()

    # 收集所有目錄的檔案
    all_files: List[Tuple[Path, datetime, int]] = []
    for rec_dir in recording_dirs:
        path = Path(rec_dir)
        if not path.exists():
            logger.info(f"[cleaner] 目錄不存在，跳過：{path}")
            continue
        dir_files = get_recording_files(path)
        logger.info(f"[cleaner] {path.name}: {len(dir_files)} 個檔案，{sum(f[2] for f in dir_files) / (1024**3):.2f} GiB")
        all_files.extend(dir_files)

    if not all_files:
        logger.info("[cleaner] 所有目錄都沒有找到錄影檔案")
        return stats

    # 全域排序（最舊的在前）
    all_files.sort(key=lambda x: x[1])

    stats.total_files = len(all_files)
    stats.total_size_bytes = sum(f[2] for f in all_files)
    current_size_gib = stats.total_size_bytes / (1024**3)
    logger.info(f"[cleaner] 合計 {stats.total_files} 個檔案，總大小：{current_size_gib:.2f} GiB")

    max_bytes = max_gib * (1024**3)
    target_bytes = target_gib * (1024**3)

    files_to_delete: List[Tuple[Path, str]] = []

    # 條件 A：容量超過 — 刪除最舊的直到目標容量
    if stats.total_size_bytes > max_bytes:
        logger.info(f"[cleaner] 容量超過限制（{current_size_gib:.2f} > {max_gib} GiB），依容量清理...")
        stats.reason = "capacity"

        current_size = stats.total_size_bytes
        for file_path, file_ts, file_size in all_files:
            if current_size <= target_bytes:
                break
            files_to_delete.append((file_path, "capacity"))
            current_size -= file_size

    # 條件 B：容量未超過 — 刪除超過保留天數的檔案
    else:
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        logger.info(f"[cleaner] 檢查保留期限（早於 {cutoff_date.date()} 的檔案）...")

        for file_path, file_ts, file_size in all_files:
            if file_ts < cutoff_date:
                files_to_delete.append((file_path, "retention"))

        if files_to_delete:
            stats.reason = "retention"
        else:
            stats.reason = "none"

    # 刪除
    if not files_to_delete:
        logger.info("[cleaner] 沒有需要刪除的檔案")
        return stats

    logger.info(f"[cleaner] {'將刪除' if dry_run else '正在刪除'} {len(files_to_delete)} 個檔案...")

    for file_path, reason in files_to_delete:
        try:
            file_size = file_path.stat().st_size
            if not dry_run:
                file_path.unlink()
                logger.info(f"[cleaner] 已刪除：{file_path.parent.name}/{file_path.name}（{file_size / (1024**2):.1f} MiB）[{reason}]")
            else:
                logger.info(f"[cleaner] 將刪除：{file_path.parent.name}/{file_path.name}（{file_size / (1024**2):.1f} MiB）[{reason}]")
            stats.deleted_files += 1
            stats.deleted_size_bytes += file_size
        except Exception as e:
            logger.error(f"[cleaner] 刪除 {file_path} 失敗：{e}")

    deleted_gib = stats.deleted_size_bytes / (1024**3)
    logger.info(f"[cleaner] {'將釋放' if dry_run else '已釋放'}：{deleted_gib:.2f} GiB")

    return stats


def run_cleanup_from_env(dry_run: bool = False) -> CleanupStats:
    """
    使用環境變數設定執行清理。

    環境變數：
        RECORDING_DIRS: 逗號分隔的多個錄影目錄（優先使用）
        RECORDING_DIR: 單一錄影目錄（向後相容）
        CLEANUP_MAX_GIB: 所有目錄合計容量上限（預設：50）
        CLEANUP_TARGET_GIB: 目標容量（預設：40）
        CLEANUP_RETENTION_DAYS: 保留天數（預設：7）
    """
    # 多目錄支援
    recording_dirs_str = os.getenv("RECORDING_DIRS", "")
    if recording_dirs_str:
        recording_dirs = [d.strip() for d in recording_dirs_str.split(",") if d.strip()]
    else:
        recording_dirs = [os.getenv("RECORDING_DIR", "/mnt/usb/recordings/cam")]

    max_gib = float(os.getenv("CLEANUP_MAX_GIB", "50"))
    target_gib = float(os.getenv("CLEANUP_TARGET_GIB", "40"))
    retention_days = int(os.getenv("CLEANUP_RETENTION_DAYS", "7"))

    logger.info(f"[cleaner] 設定：dirs={recording_dirs}, max={max_gib}GiB, target={target_gib}GiB, retention={retention_days}天")

    if len(recording_dirs) == 1:
        return cleanup_recordings(
            recording_dir=recording_dirs[0],
            max_gib=max_gib,
            target_gib=target_gib,
            retention_days=retention_days,
            dry_run=dry_run
        )
    else:
        return cleanup_multi_recordings(
            recording_dirs=recording_dirs,
            max_gib=max_gib,
            target_gib=target_gib,
            retention_days=retention_days,
            dry_run=dry_run
        )


# 允許作為獨立程式執行
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    parser = argparse.ArgumentParser(description="MuMuCam 錄影清理")
    parser.add_argument("--dry-run", action="store_true", help="顯示將刪除的內容但不實際刪除")
    parser.add_argument("--verbose", "-v", action="store_true", help="啟用除錯日誌")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    stats = run_cleanup_from_env(dry_run=args.dry_run)

    print(f"\n--- 清理摘要 ---")
    print(f"總檔案數：{stats.total_files}")
    print(f"總大小：{stats.total_size_bytes / (1024**3):.2f} GiB")
    print(f"刪除檔案數：{stats.deleted_files}")
    print(f"釋放空間：{stats.deleted_size_bytes / (1024**3):.2f} GiB")
    print(f"清理原因：{stats.reason or 'none'}")
