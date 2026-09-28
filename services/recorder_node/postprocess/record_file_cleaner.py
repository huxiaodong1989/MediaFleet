import asyncio
import logging
import os
import shutil
import threading
from datetime import datetime, timedelta
from typing import Callable, Dict, Any, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_platform.common.config import get_settings
from media_platform.infrastructure.database.models import MediaTaskModel

logger = logging.getLogger(__name__)


class RecordFileCleaner:
    """录制残留文件兜底清理器

    成功任务的精确原片由后处理 CLEANUP 阶段立即删除。本清理器只定期扫描
    ZLMediaKit 录制目录，兜底删除正常流程删除失败且超过保留天数的遗留文件。
    默认在每天凌晨指定时刻执行扫描，避免对白天业务产生性能影响。

    目录结构:
        {record_base_path}/{app_id}/{stream_id}/{date_str}/{*.mp4}

    清理策略:
        1. 按日期目录名判断（YYYY-MM-DD），超过 keep_days 的整目录删除
        2. 无法按日期解析的目录，回退到文件 mtime 判断
        3. 清理后空的 stream_id / app_id 目录自动移除
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not hasattr(self, '_initialized') or not self._initialized:
            logger.info("初始化 RecordFileCleaner 单例")
            self.settings = get_settings()
            self.running = False
            self._cleanup_task = None
            self._last_cleanup_time = None
            self._last_cleanup_result = None
            self.session_factory: Callable[[], Session] | None = None
            self.node_id: str | None = None
            self._initialized = True

    def configure(
        self,
        *,
        session_factory: Callable[[], Session],
        node_id: str,
    ) -> None:
        """注入 MySQL 任务事实查询，供删除前保护未完成和失败任务原片。"""

        self.session_factory = session_factory
        self.node_id = str(node_id or "").strip() or None

    async def start(self):
        """启动清理器"""
        if self.running:
            logger.warning("录制文件清理器已在运行")
            return

        cfg = self.settings.record_cleanup
        if not cfg.enabled:
            logger.info("录制文件清理功能已禁用")
            return

        self.running = True
        logger.info(
            f"启动录制文件清理器, 每天 {cfg.scan_hour}:00 执行扫描, "
            f"保留: {cfg.keep_days}天, 目录: {cfg.record_base_path}, "
            f"dry_run: {cfg.dry_run}"
        )
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        """停止清理器"""
        if not self.running:
            return
        self.running = False
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("录制文件清理器已停止")

    def _seconds_until_next_scan(self) -> float:
        """计算距离下一次扫描时间点的秒数"""
        now = datetime.now()
        scan_hour = self.settings.record_cleanup.scan_hour
        target = now.replace(hour=scan_hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def _cleanup_loop(self):
        """清理循环 — 每天在指定时刻执行一次"""
        while self.running:
            try:
                wait_seconds = self._seconds_until_next_scan()
                next_run = datetime.now() + timedelta(seconds=wait_seconds)
                logger.info(
                    f"录制文件清理器将在 {next_run.strftime('%Y-%m-%d %H:%M:%S')} 执行下次扫描 "
                    f"(等待 {wait_seconds / 3600:.1f} 小时)"
                )
                await asyncio.sleep(wait_seconds)

                if not self.running:
                    break

                logger.info("开始执行录制文件定时清理...")
                result = await self._do_cleanup()
                self._last_cleanup_time = datetime.now()
                self._last_cleanup_result = result
                logger.info(f"录制文件定时清理完成: {result}")

            except asyncio.CancelledError:
                logger.info("录制文件清理循环被取消")
                break
            except Exception as e:
                logger.error(f"录制文件清理异常: {e}", exc_info=True)
                await asyncio.sleep(300)

    async def _do_cleanup(self) -> Dict[str, Any]:
        """在线程池中执行清理，避免阻塞事件循环"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._scan_and_clean)

    def _scan_and_clean(self) -> Dict[str, Any]:
        """扫描录制目录并清理过期文件"""
        cfg = self.settings.record_cleanup
        base_path = cfg.record_base_path
        keep_days = cfg.keep_days
        dry_run = cfg.dry_run

        cutoff_date = datetime.now() - timedelta(days=keep_days)
        cutoff_date_str = cutoff_date.strftime("%Y-%m-%d")

        result = {
            "scanned_apps": 0,
            "scanned_streams": 0,
            "deleted_date_dirs": 0,
            "deleted_files_by_mtime": 0,
            "deleted_empty_dirs": 0,
            "freed_bytes": 0,
            "errors": [],
            "dry_run": dry_run,
            "cutoff_date": cutoff_date_str,
            "protected_streams": 0,
            "skipped_protected_streams": 0,
        }

        if not os.path.isdir(base_path):
            msg = f"录制根目录不存在: {base_path}"
            logger.warning(msg)
            result["errors"].append(msg)
            return result

        protected_streams = self._load_protected_streams()
        if protected_streams is None:
            msg = "查询录制任务保护状态失败，本轮清理已安全跳过"
            logger.error(msg)
            result["errors"].append(msg)
            return result
        result["protected_streams"] = len(protected_streams)

        for app_id in self._safe_listdir(base_path):
            app_dir = os.path.join(base_path, app_id)
            if not os.path.isdir(app_dir):
                continue
            result["scanned_apps"] += 1

            for stream_id in self._safe_listdir(app_dir):
                stream_dir = os.path.join(app_dir, stream_id)
                if not os.path.isdir(stream_dir):
                    continue
                result["scanned_streams"] += 1
                if (app_id, stream_id) in protected_streams:
                    result["skipped_protected_streams"] += 1
                    logger.info(
                        "跳过受任务状态保护的录像目录: app=%s, stream_id=%s",
                        app_id,
                        stream_id,
                    )
                    continue

                for entry_name in self._safe_listdir(stream_dir):
                    entry_path = os.path.join(stream_dir, entry_name)

                    if not os.path.isdir(entry_path):
                        self._try_delete_old_file(entry_path, cutoff_date, dry_run, result)
                        continue

                    parsed_date = self._parse_date_dir(entry_name)
                    if parsed_date and parsed_date < cutoff_date:
                        freed = self._get_dir_size(entry_path)
                        if dry_run:
                            logger.info(
                                f"[DRY_RUN] 将删除日期目录: {entry_path}, "
                                f"大小: {freed / 1024 / 1024:.1f}MB"
                            )
                        else:
                            try:
                                shutil.rmtree(entry_path)
                                logger.info(
                                    f"已删除过期日期目录: {entry_path}, "
                                    f"释放: {freed / 1024 / 1024:.1f}MB"
                                )
                            except Exception as e:
                                logger.error(f"删除目录失败: {entry_path}, {e}")
                                result["errors"].append(str(e))
                                continue
                        result["deleted_date_dirs"] += 1
                        result["freed_bytes"] += freed

                    elif parsed_date is None:
                        for fname in self._safe_listdir(entry_path):
                            fpath = os.path.join(entry_path, fname)
                            self._try_delete_old_file(fpath, cutoff_date, dry_run, result)
                        if not dry_run and os.path.isdir(entry_path) and not os.listdir(entry_path):
                            try:
                                os.rmdir(entry_path)
                                result["deleted_empty_dirs"] += 1
                            except Exception:
                                pass

                # 清理空的 stream_id 目录
                if not dry_run and os.path.isdir(stream_dir) and not os.listdir(stream_dir):
                    try:
                        os.rmdir(stream_dir)
                        result["deleted_empty_dirs"] += 1
                        logger.info(f"已删除空流目录: {stream_dir}")
                    except Exception:
                        pass

            # 清理空的 app_id 目录
            if not dry_run and os.path.isdir(app_dir) and not os.listdir(app_dir):
                try:
                    os.rmdir(app_dir)
                    result["deleted_empty_dirs"] += 1
                    logger.info(f"已删除空应用目录: {app_dir}")
                except Exception:
                    pass

        freed_mb = result["freed_bytes"] / 1024 / 1024
        logger.info(
            f"清理统计: 扫描 {result['scanned_apps']} 个应用 / "
            f"{result['scanned_streams']} 个流, "
            f"删除 {result['deleted_date_dirs']} 个日期目录, "
            f"删除 {result['deleted_files_by_mtime']} 个散落文件, "
            f"清理 {result['deleted_empty_dirs']} 个空目录, "
            f"释放 {freed_mb:.1f} MB"
        )
        return result

    def _load_protected_streams(self) -> set[tuple[str, str]] | None:
        """读取不得由兜底清理器删除原片的任务流集合。

        数据库不可用时返回 ``None``，调用方必须 fail-safe 跳过整轮删除。失败任务
        在人工重试完成或明确取消前持续受保护，避免原片先于恢复机制消失。
        """

        if self.session_factory is None:
            logger.error("录制文件清理器未配置数据库会话，拒绝执行删除")
            return None
        try:
            statement = select(
                MediaTaskModel.recording_app,
                MediaTaskModel.recording_stream_id,
            ).where(
                MediaTaskModel.task_type == "record.stream",
                MediaTaskModel.status.in_(
                    ("pending", "processing", "post_processing", "failed")
                ),
                MediaTaskModel.recording_app.is_not(None),
                MediaTaskModel.recording_stream_id.is_not(None),
            )
            if self.node_id:
                statement = statement.where(
                    MediaTaskModel.executor_node_id == self.node_id
                )
            with self.session_factory() as session:
                rows = session.execute(statement).all()
            return {
                (str(app), str(stream_id))
                for app, stream_id in rows
                if app and stream_id
            }
        except Exception:
            logger.exception("查询受保护录制任务失败")
            return None

    def _try_delete_old_file(
        self, file_path: str, cutoff_date: datetime,
        dry_run: bool, result: Dict
    ):
        """按 mtime 判断并删除单个过期文件"""
        try:
            if not os.path.isfile(file_path):
                return
            mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
            if mtime >= cutoff_date:
                return

            fsize = os.path.getsize(file_path)
            if dry_run:
                logger.info(
                    f"[DRY_RUN] 将删除文件: {file_path}, "
                    f"修改时间: {mtime.strftime('%Y-%m-%d %H:%M:%S')}, "
                    f"大小: {fsize / 1024 / 1024:.1f}MB"
                )
            else:
                os.remove(file_path)
                logger.info(
                    f"已删除过期文件: {file_path}, "
                    f"修改时间: {mtime.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            result["deleted_files_by_mtime"] += 1
            result["freed_bytes"] += fsize
        except Exception as e:
            logger.error(f"删除文件失败: {file_path}, {e}")
            result["errors"].append(str(e))

    @staticmethod
    def _parse_date_dir(name: str):
        """解析日期目录名，支持 YYYY-MM-DD 格式"""
        try:
            return datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def _get_dir_size(path: str) -> int:
        """递归计算目录大小"""
        total = 0
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
        return total

    @staticmethod
    def _safe_listdir(path: str) -> List[str]:
        try:
            return os.listdir(path)
        except Exception:
            return []

    async def manual_cleanup(self, keep_days: int = None) -> Dict[str, Any]:
        """手动执行一次清理（供 admin API 调用）"""
        original = self.settings.record_cleanup.keep_days
        if keep_days is not None:
            self.settings.record_cleanup.keep_days = keep_days
        try:
            result = await self._do_cleanup()
            self._last_cleanup_time = datetime.now()
            self._last_cleanup_result = result
            return result
        finally:
            self.settings.record_cleanup.keep_days = original

    def get_status(self) -> Dict[str, Any]:
        """获取清理器运行状态"""
        cfg = self.settings.record_cleanup
        status = {
            "running": self.running,
            "enabled": cfg.enabled,
            "scan_hour": f"{cfg.scan_hour}:00",
            "keep_days": cfg.keep_days,
            "record_base_path": cfg.record_base_path,
            "dry_run": cfg.dry_run,
            "cleanup_task_running": (
                self._cleanup_task is not None
                and not self._cleanup_task.done()
            ),
            "last_cleanup_time": (
                self._last_cleanup_time.strftime("%Y-%m-%d %H:%M:%S")
                if self._last_cleanup_time else None
            ),
            "last_cleanup_result": self._last_cleanup_result,
        }

        if self.running:
            next_seconds = self._seconds_until_next_scan()
            next_time = datetime.now() + timedelta(seconds=next_seconds)
            status["next_scan_time"] = next_time.strftime("%Y-%m-%d %H:%M:%S")

        return status
