"""离线识别处理器的新旧参数兼容测试。"""

import pytest

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.application.runtime import register_default_processors
from services.media_worker.processors.recognition import SpeechOfflineRecognizeProcessor
from services.media_worker.registry import TaskProcessorRegistry


class FakeHttpClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class FakeMediaRecog:
    """模拟 MediaRecog，不加载 FunASR/GLM、不下载文件。"""

    def __init__(self):
        self.http_client = FakeHttpClient()
        self.calls = []

    async def recog(
        self,
        stage,
        file,
        sd_switch="no",
        output_dir=None,
        dest_text=None,
        dest_spk=None,
        start_ost=0,
        end_ost=0,
        output_file=None,
        config=None,
        lang="zh",
        provider=None,
    ):
        self.calls.append(
            {
                "stage": stage,
                "file": file,
                "config": config,
                "lang": lang,
                "provider": provider,
            }
        )
        return "1\n00:00:00,000 --> 00:00:01,000\n测试字幕\n"


def _message(task_type, params):
    return TaskDispatchMessage(
        task_id="task-1",
        school_code="school-1",
        task_type=task_type,
        routing_key=task_type,
        params=params,
    )


def test_processor_accepts_new_params_and_closes_http_client():
    fake = FakeMediaRecog()
    processor = SpeechOfflineRecognizeProcessor(lambda: fake)

    result = processor.process(
        _message(
            "speech.offline.recognize",
            {
                "media_url": "https://files.example/video.mp4?signature=secret",
                "lang": "zh",
                "provider": "funasr",
                "hotwords": "课堂",
            },
        )
    )

    assert fake.calls == [
        {
            "stage": 1,
            "file": "https://files.example/video.mp4?signature=secret",
            "config": {"hotwords": "课堂", "provider": "funasr"},
            "lang": "zh",
            "provider": "funasr",
        }
    ]
    assert result.payload["res_srt"].startswith("1\n")
    assert result.payload["lang"] == "zh"
    assert result.artifacts == ()
    assert fake.http_client.closed is True


def test_processor_accepts_legacy_media_recog_ex_params():
    fake = FakeMediaRecog()
    processor = SpeechOfflineRecognizeProcessor(lambda: fake)

    processor.process(
        _message(
            "media_recog",
            {
                "media_url": "https://files.example/audio.wav",
                "ex_params": {
                    "lang": "en",
                    "asr_provider": "glm",
                    "hotword": "AI",
                },
            },
        )
    )

    assert fake.calls[0]["lang"] == "en"
    assert fake.calls[0]["provider"] == "glm"
    assert fake.calls[0]["config"]["hotword"] == "AI"


def test_processor_rejects_missing_media_url_before_loading_recognizer():
    processor = SpeechOfflineRecognizeProcessor(
        lambda: pytest.fail("参数无效时不应创建识别器")
    )

    with pytest.raises(TaskPermanentError, match="缺少media_url"):
        processor.process(_message("speech.offline.recognize", {"lang": "zh"}))


def test_processor_rejects_stage2_before_loading_recognizer():
    processor = SpeechOfflineRecognizeProcessor(
        lambda: pytest.fail("参数无效时不应创建识别器")
    )

    with pytest.raises(TaskPermanentError, match="stage=1"):
        processor.process(
            _message(
                "speech.offline.recognize",
                {"media_url": "https://files.example/video.mp4", "stage": 2},
            )
        )


def test_default_processors_include_offline_recognition_tasks():
    registry = TaskProcessorRegistry()

    register_default_processors(registry)

    assert "speech.offline.recognize" in registry.task_types
    assert "recognition.media" in registry.task_types
    assert "media_recog" in registry.task_types
