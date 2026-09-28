"""独立服务入口和共享层目录边界测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_control_center_source_does_not_import_app_paths() -> None:
    """control_center 入口和装配代码不能直接依赖旧 app 包路径。"""

    service_root = ROOT / "services" / "control_center"
    offenders = []
    for path in service_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "from app." in text or "import app." in text:
            offenders.append(path.relative_to(service_root).as_posix())

    assert offenders == []


def test_control_center_source_does_not_import_recorder_node_runtime() -> None:
    """调用中心不能导入录制节点运行时实现，避免本地执行录制职责。"""

    service_root = ROOT / "services" / "control_center"
    offenders = []
    for path in service_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if (
            "from services.recorder_node" in text
            or "import services.recorder_node" in text
        ):
            offenders.append(path.relative_to(service_root).as_posix())

    assert offenders == []


def test_platform_config_real_implementation_is_not_app_path() -> None:
    """跨服务配置真实实现必须归属 media_platform。"""

    import media_platform.common.config as platform_config
    import media_platform.common.settings as platform_settings

    assert platform_config.get_settings is platform_settings.get_settings
    assert platform_settings.Settings.__module__ == "media_platform.common.settings"
    assert platform_settings.get_settings.__module__ == "media_platform.common.settings"


def test_media_platform_source_does_not_import_app_paths() -> None:
    """media_platform 真实实现不能反向依赖旧 app 包路径。"""

    service_root = ROOT / "media_platform"
    offenders = []
    for path in service_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "from app." in text or "import app." in text:
            offenders.append(path.relative_to(service_root).as_posix())

    assert offenders == []


def test_recording_result_rabbitmq_adapter_lives_in_media_platform() -> None:
    """录制结果 RabbitMQ 适配器归属 media_platform。"""

    import media_platform.infrastructure.messaging.legacy_rabbitmq as platform_rabbitmq

    assert platform_rabbitmq.RabbitMQMaster.__module__.startswith("media_platform")
    assert platform_rabbitmq.RabbitMQWorker.__module__.startswith("media_platform")


def test_observability_logger_real_implementation_is_platform_path() -> None:
    """日志工具是真实公共能力，不能继续放在旧 app.core 目录。"""

    import media_platform.infrastructure.observability.logger as platform_logger

    assert platform_logger.setup_logging.__module__.startswith("media_platform")
    assert platform_logger.get_logger.__module__.startswith("media_platform")


def test_control_center_legacy_api_support_lives_under_service_path() -> None:
    """旧 master API 支撑归属 control-center，主项目不再保留 app 兼容壳。"""

    import services.control_center.api.legacy_auth as service_auth
    import services.control_center.api.legacy_response as service_response

    assert service_auth.verify_api_key.__module__.startswith("services.control_center")
    assert service_response.ResponseModel.__module__.startswith("services.control_center")


def test_legacy_api_schemas_live_under_control_center_service() -> None:
    """历史 Swagger 入参出参模型归属 control-center 或 media_platform。"""

    import media_platform.domain.media_file.legacy_video_models as platform_video_model
    import services.control_center.api.legacy_schemas.stream as service_stream
    import services.control_center.api.legacy_schemas.videoDto as service_video

    assert service_stream.StreamRecordRequest.__module__.startswith(
        "services.control_center"
    )
    assert service_video.VideoProcessRequest.__module__.startswith(
        "services.control_center"
    )
    assert platform_video_model.ExtractAudioResult.__module__.startswith(
        "media_platform"
    )


def test_compatibility_stream_and_video_routes_live_under_control_center() -> None:
    """仍受支持的 stream/video 兼容路由归属 control-center。"""

    import services.control_center.api.legacy_routes.stream as service_stream
    import services.control_center.api.legacy_routes.video as service_video

    assert service_stream.start_stream_recording.__module__.startswith(
        "services.control_center"
    )
    assert service_video.process_video.__module__.startswith("services.control_center")


def test_compatibility_recog_and_object_routes_live_under_control_center() -> None:
    """仍受支持的识别和目标检测兼容路由归属 control-center。"""

    import services.control_center.api.legacy_routes.object_detection as service_object
    import services.control_center.api.legacy_routes.recog as service_recog

    assert service_recog.process_recog.__module__.startswith("services.control_center")
    assert service_object.process_object_detection.__module__.startswith(
        "services.control_center"
    )


def test_root_app_compatibility_package_has_been_removed() -> None:
    """根目录旧 app 兼容包已经完成迁移并删除。"""

    assert not (ROOT / "app").exists()
