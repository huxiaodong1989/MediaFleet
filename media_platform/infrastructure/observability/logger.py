import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import uuid
import threading
from contextvars import ContextVar
from typing import Optional, Dict, Any, Literal
from fastapi import Request
import json
import time
from datetime import datetime
from pathlib import Path

# 创建一个上下文变量来存储请求ID
request_id_var: ContextVar[str] = ContextVar('request_id', default='')

def setup_request_id(request_id: Optional[str] = None) -> str:
    """
    设置当前请求的跟踪ID

    :param request_id: 可选的预设请求ID，如果不提供则生成新的
    :return: 请求ID
    """
    # 如果没有提供请求ID，则生成一个新的
    if not request_id:
        request_id = str(uuid.uuid4())

    # 设置到上下文变量
    request_id_var.set(request_id)
    return request_id

def get_request_id() -> str:
    """
    获取当前请求的跟踪ID

    :return: 请求ID，如果没有则返回空字符串
    """
    return request_id_var.get()

class RequestIDLogFilter(logging.Filter):
    """
    日志过滤器，将请求ID和其他上下文信息添加到日志记录中
    """
    def filter(self, record):
        # 添加请求ID到日志记录
        record.request_id = get_request_id()
        return True

class ColoredConsoleFormatter(logging.Formatter):
    """
    彩色控制台日志格式化器
    提供更好的控制台阅读体验
    """

    # 颜色代码
    COLORS = {
        'DEBUG': '\033[36m',      # 青色
        'INFO': '\033[32m',       # 绿色
        'WARNING': '\033[33m',    # 黄色
        'ERROR': '\033[31m',      # 红色
        'CRITICAL': '\033[35m',   # 紫色
        'RESET': '\033[0m'        # 重置
    }

    def __init__(self, *args, **kwargs):
        self.use_colors = kwargs.pop('use_colors', True)
        self.service_name = kwargs.pop('service_name', 'mediafleet')
        self.node_type = kwargs.pop('node_type', 'unknown')
        super().__init__(*args, **kwargs)

    def format(self, record):
        # 获取颜色
        color = self.COLORS.get(record.levelname, '') if self.use_colors else ''
        reset = self.COLORS['RESET'] if self.use_colors else ''

        # 格式化时间戳
        timestamp = datetime.fromtimestamp(record.created).strftime('%m/%d/%y %H:%M:%S')

        # 获取请求ID
        trace_id = getattr(record, 'request_id', '')
        trace_part = f"[{trace_id[:8]}]" if trace_id else "[--------]"

        # 构建日志消息
        log_parts = [
            f"{color}[{timestamp}]",
            record.levelname.ljust(8),
            f"{self.service_name}:{self.node_type}",
            trace_part,
            f"{record.name}:{record.lineno}",
            f"{reset}{record.getMessage()}"
        ]

        formatted_message = " ".join(log_parts)

        # 添加异常信息
        if record.exc_info:
            formatted_message += "\n" + self.formatException(record.exc_info)

        return formatted_message

class JsonFormatter(logging.Formatter):
    """
    JSON格式日志格式化器
    支持更灵活的配置和更好的可读性
    """
    def __init__(self, *args, **kwargs):
        self.include_timestamps = kwargs.pop('include_timestamps', True)
        self.include_trace_id = kwargs.pop('include_trace_id', True)
        self.service_name = kwargs.pop('service_name', 'mediafleet')
        self.node_type = kwargs.pop('node_type', 'unknown')
        super(JsonFormatter, self).__init__(*args, **kwargs)

    def format(self, record):
        # 基础日志数据
        log_data = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat() if self.include_timestamps else None,
            'level': record.levelname,
            'message': record.getMessage(),
            'service': self.service_name,
            'node_type': self.node_type,
            'logger': record.name,
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }

        # 添加跟踪ID
        if self.include_trace_id:
            log_data['trace_id'] = getattr(record, 'request_id', '')

        # 添加进程和线程信息
        log_data['process_id'] = record.process
        log_data['thread_id'] = record.thread
        log_data['thread_name'] = record.threadName

        # 添加自定义字段
        if hasattr(record, 'extra_data'):
            log_data.update(record.extra_data)

        # 移除None值和空字符串
        log_data = {k: v for k, v in log_data.items() if v is not None and v != ''}

        # 添加异常信息
        if record.exc_info:
            log_data['exception'] = {
                'type': record.exc_info[0].__name__ if record.exc_info[0] else None,
                'message': str(record.exc_info[1]) if record.exc_info[1] else None,
                'traceback': self.formatException(record.exc_info)
            }

        return json.dumps(log_data, ensure_ascii=False, separators=(',', ':'))

def setup_logging(
    log_level: int = logging.INFO,
    format_type: Literal['json', 'console', 'file'] = 'console',
    service_name: str = 'mediafleet',
    node_type: str = 'unknown',
    log_dir: Optional[str] = None,
    max_file_size: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    suppress_third_party: bool = True
) -> logging.Logger:
    """
    统一配置日志系统

    :param log_level: 日志级别
    :param format_type: 日志格式类型 ('json', 'console', 'file')
    :param service_name: 服务名称
    :param node_type: 节点类型 ('master', 'worker', 'unknown')
    :param log_dir: 日志文件目录
    :param max_file_size: 日志文件最大大小
    :param backup_count: 日志文件备份数量
    :param suppress_third_party: 是否抑制第三方库日志
    :return: 配置好的logger
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除现有的处理器
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 创建请求ID过滤器
    request_id_filter = RequestIDLogFilter()

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.addFilter(request_id_filter)

    # 根据格式类型选择格式化器
    if format_type == 'json':
        console_formatter = JsonFormatter(
            service_name=service_name,
            node_type=node_type
        )
    else:
        console_formatter = ColoredConsoleFormatter(
            service_name=service_name,
            node_type=node_type,
            use_colors=True
        )

    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # 文件处理器（如果指定了日志目录）
    if log_dir:
        # 确保日志目录存在
        Path(log_dir).mkdir(parents=True, exist_ok=True)

        # 应用日志文件
        app_log_file = os.path.join(log_dir, f"{service_name}-{node_type}.log")
        file_handler = RotatingFileHandler(
            app_log_file,
            maxBytes=max_file_size,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(log_level)
        file_handler.addFilter(request_id_filter)

        # 文件日志使用JSON格式
        file_formatter = JsonFormatter(
            service_name=service_name,
            node_type=node_type
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

        # 错误日志文件
        error_log_file = os.path.join(log_dir, f"{service_name}-{node_type}-error.log")
        error_handler = RotatingFileHandler(
            error_log_file,
            maxBytes=max_file_size,
            backupCount=backup_count,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.addFilter(request_id_filter)
        error_handler.setFormatter(file_formatter)
        root_logger.addHandler(error_handler)

    # 配置第三方库日志级别
    if suppress_third_party:
        configure_third_party_loggers()

    return root_logger

def configure_third_party_loggers():
    """
    配置第三方库的日志级别，避免日志混乱
    """
    third_party_loggers = {
        'uvicorn': logging.WARNING,
        'uvicorn.access': logging.WARNING,
        'uvicorn.error': logging.WARNING,
        'fastapi': logging.WARNING,
        'multipart': logging.WARNING,
        'httpx': logging.WARNING,
        'httpcore': logging.WARNING,
        'urllib3': logging.WARNING,
        'asyncio': logging.WARNING,
        'pika': logging.WARNING,
        'sqlalchemy': logging.WARNING,
        'alembic': logging.WARNING,
        'redis': logging.WARNING,
        'aiormq': logging.WARNING,
        'aio_pika': logging.WARNING,
    }

    for logger_name, level in third_party_loggers.items():
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        # 防止日志向上传播
        logger.propagate = True


def _configure_third_party_loggers():
    """兼容旧内部调用，实际实现使用公共函数。"""

    configure_third_party_loggers()

def get_logger(name: str = None, extra_data: Optional[Dict[str, Any]] = None) -> logging.Logger:
    """
    获取配置好的logger实例

    :param name: logger名称，默认使用调用者的模块名
    :param extra_data: 额外的上下文数据
    :return: logger实例
    """
    if name is None:
        # 自动获取调用者的模块名
        import inspect
        frame = inspect.currentframe().f_back
        name = frame.f_globals.get('__name__', 'unknown')

    logger = logging.getLogger(name)

    # 如果提供了额外数据，创建适配器
    if extra_data:
        logger = LoggerAdapter(logger, extra_data)

    return logger

class LoggerAdapter(logging.LoggerAdapter):
    """
    日志适配器，用于添加额外的上下文信息
    """
    def process(self, msg, kwargs):
        # 将额外数据添加到日志记录中
        if 'extra' not in kwargs:
            kwargs['extra'] = {}

        # 合并额外数据
        if hasattr(self, 'extra') and self.extra:
            kwargs['extra'].update(self.extra)

        # 添加extra_data字段供JsonFormatter使用
        if kwargs['extra']:
            kwargs['extra']['extra_data'] = kwargs['extra'].copy()

        return msg, kwargs

# 便利函数和装饰器

def log_performance(func_name: str = None):
    """
    性能日志装饰器，记录函数执行时间

    :param func_name: 自定义函数名称
    """
    def decorator(func):
        import functools
        import asyncio

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            logger = get_logger()
            name = func_name or f"{func.__module__}.{func.__name__}"

            try:
                logger.debug(f"开始执行函数: {name}")
                result = await func(*args, **kwargs)
                duration = round((time.time() - start_time) * 1000, 2)
                logger.info(f"函数执行完成: {name}, 耗时: {duration}ms")
                return result
            except Exception as e:
                duration = round((time.time() - start_time) * 1000, 2)
                logger.error(f"函数执行失败: {name}, 耗时: {duration}ms, 错误: {str(e)}")
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            logger = get_logger()
            name = func_name or f"{func.__module__}.{func.__name__}"

            try:
                logger.debug(f"开始执行函数: {name}")
                result = func(*args, **kwargs)
                duration = round((time.time() - start_time) * 1000, 2)
                logger.info(f"函数执行完成: {name}, 耗时: {duration}ms")
                return result
            except Exception as e:
                duration = round((time.time() - start_time) * 1000, 2)
                logger.error(f"函数执行失败: {name}, 耗时: {duration}ms, 错误: {str(e)}")
                raise

        # 判断是否为异步函数
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator

def log_task_lifecycle(task_id: str, task_type: str = None):
    """
    任务生命周期日志上下文管理器

    :param task_id: 任务ID
    :param task_type: 任务类型
    """
    class TaskLogger:
        def __init__(self, task_id: str, task_type: str = None):
            self.task_id = task_id
            self.task_type = task_type
            self.logger = get_logger('task', {'task_id': task_id, 'task_type': task_type})
            self.start_time = None

        def __enter__(self):
            self.start_time = time.time()
            self.logger.info(f"任务开始: {self.task_id}, 类型: {self.task_type}")
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            duration = round((time.time() - self.start_time) * 1000, 2)
            if exc_type is None:
                self.logger.info(f"任务完成: {self.task_id}, 耗时: {duration}ms")
            else:
                self.logger.error(f"任务失败: {self.task_id}, 耗时: {duration}ms, 错误: {str(exc_val)}")
            return False  # 不抑制异常

        def log_progress(self, message: str, progress: float = None):
            """记录任务进度"""
            if progress is not None:
                self.logger.info(f"任务进度: {self.task_id}, {message}, 进度: {progress:.1%}")
            else:
                self.logger.info(f"任务进度: {self.task_id}, {message}")

    return TaskLogger(task_id, task_type)

def configure_log_format_from_env():
    """
    从环境变量配置日志格式
    支持的环境变量：
    - LOG_FORMAT: json|console|file
    - LOG_LEVEL: DEBUG|INFO|WARNING|ERROR|CRITICAL
    - LOG_DIR: 日志文件目录
    - SUPPRESS_THIRD_PARTY_LOGS: true|false
    """
    log_format = os.getenv('LOG_FORMAT', 'console').lower()
    log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_dir = os.getenv('LOG_DIR')
    suppress_third_party = os.getenv('SUPPRESS_THIRD_PARTY_LOGS', 'true').lower() == 'true'
    service_name = os.getenv('SERVICE_NAME', 'mediafleet')
    node_type = os.getenv('NODE_TYPE', 'unknown')

    try:
        log_level = getattr(logging, log_level_str)
    except AttributeError:
        log_level = logging.INFO
        print(f"警告: 无效的日志级别 '{log_level_str}', 使用默认级别 INFO")

    return setup_logging(
        log_level=log_level,
        format_type=log_format,
        service_name=service_name,
        node_type=node_type,
        log_dir=log_dir,
        suppress_third_party=suppress_third_party
    )

# 便利的日志函数
def debug(message: str, **kwargs):
    """便利的调试日志函数"""
    logger = get_logger()
    logger.debug(message, **kwargs)

def info(message: str, **kwargs):
    """便利的信息日志函数"""
    logger = get_logger()
    logger.info(message, **kwargs)

def warning(message: str, **kwargs):
    """便利的警告日志函数"""
    logger = get_logger()
    logger.warning(message, **kwargs)

def error(message: str, **kwargs):
    """便利的错误日志函数"""
    logger = get_logger()
    logger.error(message, **kwargs)

def critical(message: str, **kwargs):
    """便利的严重错误日志函数"""
    logger = get_logger()
    logger.critical(message, **kwargs)

class RequestLoggingMiddleware:
    """
    请求日志中间件，记录请求的开始和结束
    """
    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger("request")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # 获取请求信息
        request = Request(scope, receive=receive)
        method = request.method
        path = request.url.path

        # 生成请求ID
        if "headers" in scope and scope["headers"]:
            # 尝试从请求头获取请求ID
            request_id_header = next(
                (h[1].decode() for h in scope["headers"] if h[0].decode().lower() == "x-request-id"),
                None
            )
            request_id = request_id_header or str(uuid.uuid4())
        else:
            request_id = str(uuid.uuid4())

        # 设置请求ID
        setup_request_id(request_id)

        # 记录请求开始
        start_time = time.time()
        self.logger.info(f"收到请求: {method} {path}")

        # 追踪响应
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_code = message["status"]
                duration = round((time.time() - start_time) * 1000, 2)  # 毫秒
                self.logger.info(f"完成请求: {method} {path} - 状态码: {status_code} - 耗时: {duration}ms")
            await send(message)

        # 处理请求
        await self.app(scope, receive, send_wrapper)
