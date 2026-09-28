import os
import re
import sys
import copy
import shutil
import uuid
import subprocess
import time
import queue
import librosa
import logging
import argparse
import numpy as np
import soundfile as sf
from media_platform.common.config import get_settings
from media_platform.common.redaction import sanitize_url
from services.media_worker.processors.recognition.subtitle_utils import (
    generate_srt,
    generate_srt_clip,
)
from services.media_worker.processors.recognition.trans_utils import (
    pre_proc,
    proc,
    write_state,
    load_state,
    proc_spk,
    convert_pcm_to_float,
)
from media_platform.infrastructure.storage import get_enhanced_storage_service
from services.media_worker.processors.recognition.glm_asr import (
    GLMASRModelManager,
    GLMASRTranscriber,
)
import threading
from typing import Optional, Dict, Any, List
import asyncio
import httpx

try:
    import moviepy.editor as mpy
    from moviepy.editor import CompositeVideoClip, TextClip, concatenate_videoclips
    from moviepy.video.tools.subtitles import SubtitlesClip
except ImportError:
    # stage1 离线识别不再依赖 moviepy；旧的视频裁剪 stage2 使用时再给出明确错误。
    mpy = None
    CompositeVideoClip = None
    TextClip = None
    concatenate_videoclips = None
    SubtitlesClip = None


logger = logging.getLogger("video_process")

def _select_funasr_device(configured_device: str) -> str:
    """根据配置选择推理设备，auto/cuda 不可用时明确降级到 CPU。"""
    device = (configured_device or "cpu").strip().lower()
    if device == "auto":
        device = "cuda:0"

    if device.startswith("cuda"):
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                return device
            logger.warning("配置了 GPU 推理，但当前环境 CUDA 不可用，降级为 CPU")
        except Exception as exc:
            logger.warning(f"检测 CUDA 状态失败，降级为 CPU: {exc}")
        return "cpu"

    return device


def _clean_asr_text(text: Any) -> str:
    """清理 SenseVoice 等模型输出中的语言、情绪、事件标签。"""
    if text is None:
        return ""
    cleaned = str(text).strip()
    try:
        from funasr.utils.postprocess_utils import rich_transcription_postprocess

        cleaned = rich_transcription_postprocess(cleaned)
    except Exception:
        cleaned = re.sub(r"<\|[^|]+?\|>", "", cleaned)
    # SenseVoice 会把情绪/音乐事件输出为 emoji，本业务字幕只需要可读文本。
    cleaned = re.sub(r"[\U0001F300-\U0001FAFF]", "", cleaned)
    return cleaned.strip()


def _to_ms(value: Any) -> Optional[int]:
    """兼容秒和毫秒两类时间字段，统一转成毫秒。"""
    if value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    # 小数通常来自秒级字段；整数即使小于 1000 也可能是 FunASR 的毫秒。
    if not float(numeric).is_integer() and numeric < 1000:
        return int(numeric * 1000)
    return int(numeric)


def _first_present(data: Dict[str, Any], *keys: str) -> Any:
    """按顺序取第一个存在且不为 None 的字段，保留 0 这类合法值。"""
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _normalize_timestamp_pairs(value: Any) -> list:
    """把 FunASR 不同模型的 timestamp 字段统一成 [[start_ms, end_ms], ...]。"""
    if not isinstance(value, (list, tuple)):
        return []
    pairs = []
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            start_ms = _to_ms(item[0])
            end_ms = _to_ms(item[1])
        elif isinstance(item, dict):
            start_ms = _to_ms(_first_present(item, "start", "start_time", "begin"))
            end_ms = _to_ms(_first_present(item, "end", "end_time", "finish"))
        else:
            continue
        if start_ms is not None and end_ms is not None and end_ms > start_ms:
            pairs.append([start_ms, end_ms])
    return pairs


def _clean_asr_token(token: Any) -> str:
    """清理单个 SenseVoice token，保留可用于字幕展示的文本。"""
    if token is None:
        return ""
    cleaned = re.sub(r"<\|[^|]+?\|>", "", str(token))
    cleaned = re.sub(r"[\U0001F300-\U0001FAFF]", "", cleaned)
    return cleaned.strip()


def _build_sentence_info_from_words(raw_result: Dict[str, Any]) -> list:
    """从 SenseVoice 的 words + timestamp 精准构造 SRT 句段。"""
    words = raw_result.get("words")
    timestamps = _normalize_timestamp_pairs(raw_result.get("timestamp"))
    if not isinstance(words, list) or not timestamps:
        return []
    if len(words) != len(timestamps):
        logger.error(
            "SenseVoice words/timestamp 数量不一致，无法生成精准 SRT: words=%s, timestamps=%s",
            len(words),
            len(timestamps),
        )
        return []

    sentence_info = []
    current_words = []
    current_timestamps = []

    def flush():
        if not current_words or not current_timestamps:
            return
        sentence_info.append(
            {"text": current_words[:], "timestamp": current_timestamps[:]}
        )
        current_words.clear()
        current_timestamps.clear()

    for word, timestamp in zip(words, timestamps):
        token = _clean_asr_token(word)
        if not token:
            continue
        current_words.append(token)
        current_timestamps.append(timestamp)

        duration_ms = current_timestamps[-1][1] - current_timestamps[0][0]
        should_break = (
            token in {"。", "！", "？", ".", "!", "?", "；", ";"}
            or duration_ms >= 8000
            or len(current_words) >= 40
        )
        if should_break:
            flush()

    flush()
    return sentence_info


def _sentence_timestamp_from_bounds(sentence: Dict[str, Any]) -> list:
    """从 sentence_info 的 start/end 风格字段构造字幕时间戳。"""
    start_ms = _to_ms(_first_present(sentence, "start", "start_time", "begin"))
    end_ms = _to_ms(_first_present(sentence, "end", "end_time", "finish"))
    if start_ms is not None and end_ms is not None and end_ms > start_ms:
        return [[start_ms, end_ms]]
    return []


def _normalize_sentence_info(raw_result: Dict[str, Any]) -> list:
    """统一 FunASR 输出，确保每个字幕句段都有文本和毫秒时间戳。"""
    normalized = []
    sentence_info = raw_result.get("sentence_info")

    if isinstance(sentence_info, list):
        for sentence in sentence_info:
            if not isinstance(sentence, dict):
                continue
            text = _clean_asr_text(sentence.get("text"))
            timestamp = _normalize_timestamp_pairs(sentence.get("timestamp"))
            if not timestamp:
                timestamp = _sentence_timestamp_from_bounds(sentence)
            if not text or not timestamp:
                continue
            item = {"text": text, "timestamp": timestamp}
            if "spk" in sentence:
                item["spk"] = sentence["spk"]
            normalized.append(item)

    if normalized:
        return normalized

    normalized = _build_sentence_info_from_words(raw_result)
    return normalized


def _first_result(result: Any) -> Dict[str, Any]:
    """FunASR generate 通常返回 list[dict]，这里统一取第一条结果。"""
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return result[0]
    if isinstance(result, dict):
        return result
    return {}


class FunASRModelManager:
    """按模型类型、语言和设备缓存 FunASR AutoModel 池。

    FunASR AutoModel 并发调用同一个实例会污染内部缓存/张量状态。
    这里按并发上限创建固定模型池，每个 generate 调用独占租用一个实例。
    """

    _model_pools: Dict[str, queue.LifoQueue] = {}
    _pool_sizes: Dict[str, int] = {}
    _lock = threading.Lock()

    @classmethod
    def _cache_key(cls, lang: str) -> str:
        cfg = get_settings().funasr
        device = _select_funasr_device(cfg.device)
        return f"{cfg.model_type}:{lang}:{device}"

    @classmethod
    def _pool_size(cls) -> int:
        settings = get_settings()
        cfg = settings.funasr
        device = _select_funasr_device(cfg.device)
        if device.startswith(("cuda", "mps", "xpu")):
            device_limit = cfg.gpu_concurrency
        else:
            device_limit = cfg.cpu_concurrency
        return max(
            1,
            min(
                settings.rabbitmq.max_tasks,
                settings.media.recog_concurrency,
                device_limit,
            ),
        )

    @classmethod
    def _ensure_pool(cls, lang: str = "zh") -> queue.LifoQueue:
        cache_key = cls._cache_key(lang)
        desired_size = cls._pool_size()
        pool = cls._model_pools.get(cache_key)
        if pool is not None and cls._pool_sizes.get(cache_key, 0) >= desired_size:
            return pool

        with cls._lock:
            pool = cls._model_pools.get(cache_key)
            if pool is None:
                pool = queue.LifoQueue()
                cls._model_pools[cache_key] = pool
                cls._pool_sizes[cache_key] = 0

            current_size = cls._pool_sizes.get(cache_key, 0)
            if current_size < desired_size:
                logger.warning(
                    "初始化 FunASR 模型池: key=%s, current=%s, target=%s",
                    cache_key,
                    current_size,
                    desired_size,
                )
                for _ in range(current_size, desired_size):
                    pool.put(cls._build_model(lang))
                cls._pool_sizes[cache_key] = desired_size

        return pool

    @classmethod
    def get_model(cls, lang: str = "zh"):
        """兼容旧调用：返回池中的一个模型引用。新代码应使用 generate 独占租用。"""
        pool = cls._ensure_pool(lang)
        model = pool.get()
        pool.put(model)
        return model

    @classmethod
    def preload_default_model(cls):
        """启动时预热默认语言模型池，避免首批识别任务承担加载耗时。"""
        cls._ensure_pool("zh")

    @classmethod
    def _build_model(cls, lang: str):
        from funasr import AutoModel

        cfg = get_settings().funasr
        device = _select_funasr_device(cfg.device)
        vad_kwargs = {"max_single_segment_time": cfg.vad_max_segment_ms}

        if cfg.model_type == "sensevoice":
            logger.warning(
                f"初始化 SenseVoiceSmall 离线模型: model={cfg.sensevoice_model}, device={device}"
            )
            return AutoModel(
                model=cfg.sensevoice_model,
                trust_remote_code=cfg.sensevoice_trust_remote_code,
                vad_model="fsmn-vad",
                vad_kwargs=vad_kwargs,
                device=device,
                ncpu=cfg.ncpu,
                disable_update=cfg.disable_update,
            )

        if lang == "en":
            model_name = cfg.paraformer_en_model
        else:
            model_name = cfg.paraformer_zh_model

        logger.warning(
            f"初始化 Paraformer 离线模型: model={model_name}, lang={lang}, device={device}"
        )
        return AutoModel(
            model=model_name,
            vad_model=cfg.vad_model,
            vad_kwargs=vad_kwargs,
            punc_model=cfg.punc_model,
            spk_model=cfg.spk_model,
            device=device,
            ncpu=cfg.ncpu,
            disable_update=cfg.disable_update,
        )

    @classmethod
    def generate(
        cls, audio_path: str, lang: str = "zh", hotwords: Any = ""
    ) -> Dict[str, Any]:
        """按配置走 FunASR 官方离线 generate 接口并返回第一条原始结果。"""
        cfg = get_settings().funasr
        pool = cls._ensure_pool(lang)
        model = pool.get()
        start_time = time.perf_counter()

        try:
            if cfg.model_type == "sensevoice":
                result = model.generate(
                    input=audio_path,
                    cache={},
                    language=cfg.sensevoice_language if cfg.sensevoice_language else lang,
                    use_itn=cfg.sensevoice_use_itn,
                    batch_size_s=cfg.batch_size_s,
                    merge_vad=cfg.merge_vad,
                    merge_length_s=cfg.merge_length_s,
                    output_timestamp=cfg.sensevoice_output_timestamp,
                )
            else:
                result = model.generate(
                    input=audio_path,
                    cache={},
                    return_spk_res=False,
                    sentence_timestamp=True,
                    return_raw_text=True,
                    is_final=True,
                    hotword=hotwords,
                    batch_size_s=cfg.batch_size_s,
                    pred_timestamp=lang == "en",
                    en_post_proc=True,
                )
        finally:
            pool.put(model)

        elapsed = time.perf_counter() - start_time
        logger.info(
            "FunASR 离线识别完成: model_type=%s, lang=%s, pool_size=%s, elapsed=%.2fs",
            cfg.model_type,
            lang,
            cls._pool_sizes.get(cls._cache_key(lang), 1),
            elapsed,
        )
        return _first_result(result)


class VideoClipper:
    def __init__(self, funasr_model):
        logging.warning("Initializing VideoClipper.")
        self.funasr_model = funasr_model
        self.GLOBAL_COUNT = 0
        self.lang = "zh"

    @staticmethod
    def _ensure_moviepy_available():
        if mpy is None:
            raise RuntimeError("stage2 视频裁剪需要安装 moviepy")

    def recog(
        self, audio_input, sd_switch="no", state=None, hotwords="", output_dir=None
    ):
        if state is None:
            state = {}
        sr, data = audio_input

        # Convert to float64 consistently (includes data type checking)
        data = convert_pcm_to_float(data)

        # assert sr == 16000, "16kHz sample rate required, {} given.".format(sr)
        if sr != 16000:  # resample with librosa
            data = librosa.resample(data, orig_sr=sr, target_sr=16000)
        if len(data.shape) == 2:  # multi-channel wav input
            logging.warning(
                "Input wav shape: {}, only first channel reserved.".format(data.shape)
            )
            data = data[:, 0]
        state["audio_input"] = (sr, data)
        temp_output_dir = output_dir
        cleanup_temp_output_dir = False
        if temp_output_dir is None:
            temp_output_dir = os.path.join("output", uuid.uuid4().hex)
            cleanup_temp_output_dir = True
        os.makedirs(temp_output_dir, exist_ok=True)
        audio_path = os.path.join(temp_output_dir, "funasr_input_16k.wav")
        sf.write(audio_path, data, 16000)

        try:
            raw_result = FunASRModelManager.generate(audio_path, self.lang, hotwords)
        finally:
            if cleanup_temp_output_dir:
                shutil.rmtree(temp_output_dir, ignore_errors=True)

        sentence_info = _normalize_sentence_info(raw_result)
        res_srt = generate_srt(sentence_info)
        logger.info(
            "FunASR normalized result: sentences=%s",
            len(sentence_info),
        )
        if str(sd_switch).lower() == "yes" and sentence_info:
            state["sd_sentences"] = sentence_info
        state["recog_res_raw"] = raw_result.get("raw_text") or raw_result.get("text") or ""
        state["timestamp"] = raw_result.get("timestamp") or []
        state["sentences"] = sentence_info
        res_text = _clean_asr_text(raw_result.get("text") or raw_result.get("raw_text"))
        return res_text, res_srt, state

    def clip(
        self,
        dest_text,
        start_ost,
        end_ost,
        state,
        dest_spk=None,
        output_dir=None,
        timestamp_list=None,
    ):
        # get from state
        audio_input = state["audio_input"]
        recog_res_raw = state["recog_res_raw"]
        timestamp = state["timestamp"]
        sentences = state["sentences"]
        sr, data = audio_input
        data = data.astype(np.float64)

        if timestamp_list is None:
            all_ts = []
            if dest_spk is None or dest_spk == "" or "sd_sentences" not in state:
                for _dest_text in dest_text.split("#"):
                    if "[" in _dest_text:
                        match = re.search(r"\[(\d+),\s*(\d+)\]", _dest_text)
                        if match:
                            offset_b, offset_e = map(int, match.groups())
                            log_append = ""
                        else:
                            offset_b, offset_e = 0, 0
                            log_append = "(Bracket detected in dest_text but offset time matching failed)"
                        _dest_text = _dest_text[: _dest_text.find("[")]
                    else:
                        log_append = ""
                        offset_b, offset_e = 0, 0
                    _dest_text = pre_proc(_dest_text)
                    ts = proc(recog_res_raw, timestamp, _dest_text)
                    for _ts in ts:
                        all_ts.append([_ts[0] + offset_b * 16, _ts[1] + offset_e * 16])
                    if len(ts) > 1 and match:
                        log_append += (
                            "(offsets detected but No.{} sub-sentence matched to {} periods in audio, \
                            offsets are applied to all periods)"
                        )
            else:
                for _dest_spk in dest_spk.split("#"):
                    ts = proc_spk(_dest_spk, state["sd_sentences"])
                    for _ts in ts:
                        all_ts.append(_ts)
                log_append = ""
        else:
            all_ts = timestamp_list
        ts = all_ts
        # ts.sort()
        srt_index = 0
        clip_srt = ""
        if len(ts):
            start, end = ts[0]
            start = min(max(0, start + start_ost * 16), len(data))
            end = min(max(0, end + end_ost * 16), len(data))
            res_audio = data[start:end]
            start_end_info = "from {} to {}".format(start / 16000, end / 16000)
            srt_clip, _, srt_index = generate_srt_clip(
                sentences, start / 16000.0, end / 16000.0, begin_index=srt_index
            )
            clip_srt += srt_clip
            for _ts in ts[1:]:  # multiple sentence input or multiple output matched
                start, end = _ts
                start = min(max(0, start + start_ost * 16), len(data))
                end = min(max(0, end + end_ost * 16), len(data))
                start_end_info += ", from {} to {}".format(start, end)
                res_audio = np.concatenate(
                    [res_audio, data[start + start_ost * 16 : end + end_ost * 16]], -1
                )
                srt_clip, _, srt_index = generate_srt_clip(
                    sentences, start / 16000.0, end / 16000.0, begin_index=srt_index - 1
                )
                clip_srt += srt_clip
        if len(ts):
            message = (
                "{} periods found in the speech: ".format(len(ts))
                + start_end_info
                + log_append
            )
        else:
            message = "No period found in the speech, return raw speech. You may check the recognition result and try other destination text."
            res_audio = data
        return (sr, res_audio), message, clip_srt

    def video_recog(self, video_filename, sd_switch="no", hotwords="", output_dir=None):
        self._ensure_moviepy_available()
        video = mpy.VideoFileClip(video_filename)
        # Extract the base name, add '_clip.mp4', and 'wav'
        if output_dir is not None:
            os.makedirs(output_dir, exist_ok=True)
            _, base_name = os.path.split(video_filename)
            base_name, _ = os.path.splitext(base_name)
            clip_video_file = base_name + "_clip.mp4"
            audio_file = base_name + ".wav"
            audio_file = os.path.join(output_dir, audio_file)
        else:
            base_name, _ = os.path.splitext(video_filename)
            clip_video_file = base_name + "_clip.mp4"
            audio_file = base_name + ".wav"
        video.audio.write_audiofile(audio_file)
        wav = librosa.load(audio_file, sr=16000)[0]
        # delete the audio file after processing
        if os.path.exists(audio_file):
            os.remove(audio_file)
        state = {
            "video_filename": video_filename,
            "clip_video_file": clip_video_file,
            "video": video,
        }
        # res_text, res_srt = self.recog((16000, wav), state)
        return self.recog((16000, wav), sd_switch, state, hotwords, output_dir)

    def video_clip(
        self,
        dest_text,
        start_ost,
        end_ost,
        state,
        font_size=32,
        font_color="white",
        add_sub=False,
        dest_spk=None,
        output_dir=None,
        timestamp_list=None,
    ):
        self._ensure_moviepy_available()
        # get from state
        recog_res_raw = state["recog_res_raw"]
        timestamp = state["timestamp"]
        sentences = state["sentences"]
        video = state["video"]
        clip_video_file = state["clip_video_file"]
        video_filename = state["video_filename"]

        if timestamp_list is None:
            all_ts = []
            if dest_spk is None or dest_spk == "" or "sd_sentences" not in state:
                for _dest_text in dest_text.split("#"):
                    if "[" in _dest_text:
                        match = re.search(r"\[(\d+),\s*(\d+)\]", _dest_text)
                        if match:
                            offset_b, offset_e = map(int, match.groups())
                            log_append = ""
                        else:
                            offset_b, offset_e = 0, 0
                            log_append = "(Bracket detected in dest_text but offset time matching failed)"
                        _dest_text = _dest_text[: _dest_text.find("[")]
                    else:
                        offset_b, offset_e = 0, 0
                        log_append = ""
                    _dest_text = pre_proc(_dest_text)
                    ts = proc(recog_res_raw, timestamp, _dest_text)
                    for _ts in ts:
                        all_ts.append([_ts[0] + offset_b * 16, _ts[1] + offset_e * 16])
                    if len(ts) > 1 and match:
                        log_append += (
                            "(offsets detected but No.{} sub-sentence matched to {} periods in audio, \
                            offsets are applied to all periods)"
                        )
            else:
                for _dest_spk in dest_spk.split("#"):
                    ts = proc_spk(_dest_spk, state["sd_sentences"])
                    for _ts in ts:
                        all_ts.append(_ts)
        else:  # AI clip pass timestamp as input directly
            all_ts = [[i[0] * 16.0, i[1] * 16.0] for i in timestamp_list]

        srt_index = 0
        time_acc_ost = 0.0
        ts = all_ts
        # ts.sort()
        clip_srt = ""
        if len(ts):
            if self.lang == "en":
                sentences = sentences.split()
            start, end = ts[0][0] / 16000, ts[0][1] / 16000
            srt_clip, subs, srt_index = generate_srt_clip(
                sentences, start, end, begin_index=srt_index, time_acc_ost=time_acc_ost
            )
            start, end = start + start_ost / 1000.0, end + end_ost / 1000.0
            video_clip = video.subclip(start, end)
            start_end_info = "from {} to {}".format(start, end)
            clip_srt += srt_clip
            if add_sub:
                generator = lambda txt: TextClip(
                    txt,
                    font="./font/STHeitiMedium.ttc",
                    fontsize=font_size,
                    color=font_color,
                )
                subtitles = SubtitlesClip(subs, generator)
                video_clip = CompositeVideoClip(
                    [video_clip, subtitles.set_pos(("center", "bottom"))]
                )
            concate_clip = [video_clip]
            time_acc_ost += end + end_ost / 1000.0 - (start + start_ost / 1000.0)
            for _ts in ts[1:]:
                start, end = _ts[0] / 16000, _ts[1] / 16000
                srt_clip, subs, srt_index = generate_srt_clip(
                    sentences,
                    start,
                    end,
                    begin_index=srt_index - 1,
                    time_acc_ost=time_acc_ost,
                )
                chi_subs = []
                sub_starts = subs[0][0][0]
                for sub in subs:
                    chi_subs.append(
                        ((sub[0][0] - sub_starts, sub[0][1] - sub_starts), sub[1])
                    )
                start, end = start + start_ost / 1000.0, end + end_ost / 1000.0
                _video_clip = video.subclip(start, end)
                start_end_info += ", from {} to {}".format(start, end)
                clip_srt += srt_clip
                if add_sub:
                    generator = lambda txt: TextClip(
                        txt,
                        font="./font/STHeitiMedium.ttc",
                        fontsize=font_size,
                        color=font_color,
                    )
                    subtitles = SubtitlesClip(chi_subs, generator)
                    _video_clip = CompositeVideoClip(
                        [_video_clip, subtitles.set_pos(("center", "bottom"))]
                    )
                    # _video_clip.write_videofile("debug.mp4", audio_codec="aac")
                concate_clip.append(copy.copy(_video_clip))
                time_acc_ost += end + end_ost / 1000.0 - (start + start_ost / 1000.0)
            message = "{} periods found in the audio: ".format(len(ts)) + start_end_info
            logging.warning("Concating...")
            if len(concate_clip) > 1:
                video_clip = concatenate_videoclips(concate_clip)
            # clip_video_file = clip_video_file[:-4] + '_no{}.mp4'.format(self.GLOBAL_COUNT)
            if output_dir is not None:
                os.makedirs(output_dir, exist_ok=True)
                _, file_with_extension = os.path.split(clip_video_file)
                clip_video_file_name, _ = os.path.splitext(file_with_extension)
                print(output_dir, clip_video_file)
                clip_video_file = os.path.join(
                    output_dir,
                    "{}_no{}.mp4".format(clip_video_file_name, self.GLOBAL_COUNT),
                )
                temp_audio_file = os.path.join(
                    output_dir,
                    "{}_tempaudio_no{}.mp4".format(
                        clip_video_file_name, self.GLOBAL_COUNT
                    ),
                )
            else:
                clip_video_file = clip_video_file[:-4] + "_no{}.mp4".format(
                    self.GLOBAL_COUNT
                )
                temp_audio_file = clip_video_file[:-4] + "_tempaudio_no{}.mp4".format(
                    self.GLOBAL_COUNT
                )
            video_clip.write_videofile(
                clip_video_file, audio_codec="aac", temp_audiofile=temp_audio_file
            )
            self.GLOBAL_COUNT += 1
        else:
            clip_video_file = video_filename
            message = "No period found in the audio, return raw speech. You may check the recognition result and try other destination text."
            srt_clip = ""
        return clip_video_file, message, clip_srt


class MediaRecog:
    _funasr_models = {}  # 改为字典，支持多语言模型缓存
    _model_lock = threading.Lock()  # 线程锁确保线程安全

    def __init__(self):
        self.storage_service = get_enhanced_storage_service()
        self.http_client = httpx.AsyncClient(timeout=30.0)

    @classmethod
    def _get_funasr_model(cls, lang="zh"):
        """根据语言获取或初始化 FunASR 模型实例（单例模式，支持多语言）"""
        return FunASRModelManager.get_model(lang)

    @staticmethod
    def preload_default_model(provider: Optional[str] = None):
        """给 worker 启动预热使用，避免首个任务慢在模型加载。"""
        selected_provider = MediaRecog._normalize_asr_provider(
            provider or get_settings().asr.provider
        )
        if selected_provider == "glm":
            GLMASRModelManager.preload_default_model()
            return
        FunASRModelManager.preload_default_model()

    @staticmethod
    def _normalize_asr_provider(provider: Optional[str]) -> str:
        normalized = (provider or "funasr").strip().lower()
        if normalized in {"glm_asr", "glm-asr", "glm_asr_nano", "glm-asr-nano"}:
            return "glm"
        if normalized not in {"funasr", "glm"}:
            logger.warning("未知 ASR provider=%s，回退到 funasr", provider)
            return "funasr"
        return normalized

    @staticmethod
    def _cleanup_files(file_path: Optional[str] = None, output_dir: Optional[str] = None):
        """清理下载的临时文件和输出目录"""
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"已清理下载文件: {file_path}")
            except OSError as e:
                logger.warning(f"清理下载文件失败: {file_path}, 错误: {e}")

        if output_dir and os.path.exists(output_dir):
            try:
                shutil.rmtree(output_dir)
                logger.info(f"已清理输出目录: {output_dir}")
            except OSError as e:
                logger.warning(f"清理输出目录失败: {output_dir}, 错误: {e}")

    @staticmethod
    def _prepare_audio_for_offline_recog(file_path: str, output_dir: str) -> str:
        """把任意音视频统一转成 16k 单声道 wav，降低模型输入差异。"""
        settings = get_settings()
        audio_path = os.path.join(output_dir, "funasr_input_16k.wav")
        cmd = [
            settings.media.ffmpeg_path,
            "-y",
            "-i",
            file_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            audio_path,
        ]
        logger.info(f"开始准备 FunASR 离线音频: {audio_path}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"ffmpeg 音频转换失败: {result.stderr}")
            raise RuntimeError("FunASR 离线识别前音频转换失败")
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            raise RuntimeError("FunASR 离线识别音频文件为空")
        return audio_path

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
        auto_cleanup_output = output_dir is None
        if output_dir is None:
            output_dir = os.path.join("output", uuid.uuid4().hex)

        file_path = None
        try:
            logger.info(f"开始下载媒体文件{file}")
            file_path = await self.storage_service.download_file(file)
            logger.info(f"文件下载成功:{file_path}")
            if not file_path:
                logger.error(f"文件下载失败{file}")
                return None

            audio_suffixs = [".wav", ".mp3", ".aac", ".m4a", ".flac"]
            video_suffixs = [
                ".mp4",
                ".avi",
                ".mkv",
                ".flv",
                ".mov",
                ".webm",
                ".ts",
                ".mpeg",
            ]
            _, ext = os.path.splitext(file_path)
            if ext.lower() in audio_suffixs:
                mode = "audio"
            elif ext.lower() in video_suffixs:
                mode = "video"
            else:
                logger.error(f"Unsupported file format: {file_path}, supported: {audio_suffixs + video_suffixs}")
                return None
            while output_dir.endswith("/"):
                output_dir = output_dir[:-1]
            os.makedirs(output_dir, exist_ok=True)
            if stage == 1:
                audio_path = self._prepare_audio_for_offline_recog(file_path, output_dir)
                recog_config = config if isinstance(config, dict) else {}
                selected_provider = self._normalize_asr_provider(
                    provider
                    or recog_config.get("asr_provider")
                    or recog_config.get("provider")
                    or get_settings().asr.provider
                )
                if selected_provider == "glm":
                    glm_result = await asyncio.to_thread(
                        GLMASRTranscriber(recog_config).transcribe,
                        audio_path,
                        output_dir,
                    )
                    if not glm_result.srt:
                        logger.error("GLM-ASR 未生成有效 SRT: meta=%s", glm_result.meta)
                        return None

                    total_srt_file = output_dir + "/total.srt"
                    with open(total_srt_file, "w", encoding="utf-8") as fout:
                        fout.write(glm_result.srt)
                        logging.warning("Write total subtitle to {}".format(total_srt_file))
                    write_state(
                        output_dir,
                        {
                            "recog_res_raw": glm_result.text,
                            "timestamp": [],
                            "sentences": glm_result.sentence_info,
                            "provider": "glm",
                            "meta": glm_result.meta,
                        },
                    )
                    logger.info(
                        "GLM-ASR recognition successed. text_len=%s, srt_segments=%s, meta=%s",
                        len(glm_result.text),
                        len(glm_result.sentence_info),
                        glm_result.meta,
                    )
                    return glm_result.srt

                if isinstance(config, dict):
                    hotwords = recog_config.get("hotwords") or recog_config.get("hotword") or ""
                else:
                    hotwords = config or ""
                raw_result = await asyncio.to_thread(
                    FunASRModelManager.generate, audio_path, lang, hotwords
                )
                sentence_info = _normalize_sentence_info(raw_result)
                if not sentence_info:
                    logger.error(
                        f"FunASR 未返回有效时间戳，无法生成业务所需 SRT: keys={list(raw_result.keys())}"
                    )
                    return None

                res_text = _clean_asr_text(
                    raw_result.get("text") or raw_result.get("raw_text")
                )
                res_srt = generate_srt(sentence_info)
                state = {
                    "recog_res_raw": raw_result.get("raw_text")
                    or raw_result.get("text")
                    or "",
                    "timestamp": raw_result.get("timestamp") or [],
                    "sentences": sentence_info,
                }
                total_srt_file = output_dir + "/total.srt"
                with open(total_srt_file, "w", encoding="utf-8") as fout:
                    fout.write(res_srt)
                    logging.warning("Write total subtitle to {}".format(total_srt_file))
                write_state(output_dir, state)
                logging.warning(
                    f"Recognition successed. text_len={len(res_text)}, srt_segments={len(sentence_info)}"
                )
                return res_srt
            if stage == 2:
                audio_clipper = VideoClipper(None)
                if mode == "audio":
                    state = load_state(output_dir)
                    wav, sr = librosa.load(file_path, sr=16000)
                    state["audio_input"] = (sr, wav)
                    (sr, audio), message, srt_clip = audio_clipper.clip(
                        dest_text, start_ost, end_ost, state, dest_spk=dest_spk
                    )
                    if output_file is None:
                        output_file = output_dir + "/result.wav"
                    clip_srt_file = output_file[:-3] + "srt"
                    logging.warning(message)
                    sf.write(output_file, audio, 16000)
                    assert output_file.endswith(".wav"), "output_file must ends with '.wav'"
                    logging.warning("Save clipped wav file to {}".format(output_file))
                    with open(clip_srt_file, "w") as fout:
                        fout.write(srt_clip)
                        logging.warning(
                            "Write clipped subtitle to {}".format(clip_srt_file)
                        )
                if mode == "video":
                    state = load_state(output_dir)
                    state["video_filename"] = file_path
                    if output_file is None:
                        state["clip_video_file"] = file_path[:-4] + "_clip.mp4"
                    else:
                        state["clip_video_file"] = output_file
                    clip_srt_file = state["clip_video_file"][:-3] + "srt"
                    state["video"] = mpy.VideoFileClip(file_path)
                    clip_video_file, message, srt_clip = audio_clipper.video_clip(
                        dest_text, start_ost, end_ost, state, dest_spk=dest_spk
                    )
                    logging.warning("Clipping Log: {}".format(message))
                    logging.warning("Save clipped mp4 file to {}".format(clip_video_file))
                    with open(clip_srt_file, "w") as fout:
                        fout.write(srt_clip)
                        logging.warning(
                            "Write clipped subtitle to {}".format(clip_srt_file)
                        )
        finally:
            self._cleanup_files(
                file_path,
                output_dir if auto_cleanup_output else None,
            )

    async def callback_notification(
        self, task_id: str, callback_url: str, result: Dict[str, Any]
    ) -> bool:
        """
        发送回调通知
        :param task_id: 任务ID
        :param result: 任务结果
        :return: 是否发送成功
        """
        try:
            if not callback_url:
                logger.info(f"没有设置回调URL，跳过回调: {task_id}")
                return False

            # 构建回调数据
            # callback_data = {
            #     "task_id": self.task_id,
            #     "status": "completed",
            #     "progress": 100,
            #     "result": result,
            #     "error_message": "",
            #     "message": "视频处理完成",
            # }

            logger.info("回调数据已构造: task_id=%s", task_id)

            # 发送回调请求，支持重试
            max_retries = 3
            retry_interval = 5  # 秒

            for retry in range(max_retries):
                try:
                    logger.info(
                        "开始发送回调通知(尝试 %s/%s): URL=%s, 任务ID=%s",
                        retry + 1,
                        max_retries,
                        sanitize_url(callback_url),
                        task_id,
                    )

                    # 发送HTTP POST请求
                    response = await self.http_client.post(
                        callback_url,
                        json=result,
                        headers={"Content-Type": "application/json"},
                        timeout=30.0,
                    )

                    # 检查响应状态
                    if response.status_code < 300:
                        logger.info(f"回调通知发送成功: {task_id}")
                        return True
                    else:
                        logger.warning(
                            f"回调通知发送失败(尝试 {retry + 1}/{max_retries}): {task_id}, 状态码: {response.status_code}, 响应: {response.text}"
                        )

                        # 最后一次重试失败
                        if retry == max_retries - 1:
                            logger.error(
                                f"回调通知达到最大重试次数，放弃发送: {task_id}"
                            )
                            return False

                        # 等待一段时间后重试
                        await asyncio.sleep(retry_interval)

                except Exception as e:
                    logger.warning(
                        f"回调通知异常(尝试 {retry + 1}/{max_retries}): {task_id}, 错误: {str(e)}"
                    )

                    # 最后一次重试失败
                    if retry == max_retries - 1:
                        logger.error(f"回调通知达到最大重试次数，放弃发送: {task_id}")
                        return False

                    # 等待一段时间后重试
                    await asyncio.sleep(retry_interval)

            return False

        except Exception as e:
            logger.error(f"发送回调通知异常: {task_id}, 错误: {str(e)}", exc_info=True)
            return False
