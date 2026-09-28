import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from media_platform.common.config import get_settings
from services.media_worker.processors.recognition.subtitle_utils import generate_srt


@dataclass
class GLMASRResult:
    text: str
    srt: str
    sentence_info: List[Dict[str, Any]]
    meta: Dict[str, Any]


def _first_present(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _extract_vad_pairs(data: Any) -> List[Tuple[int, int]]:
    if isinstance(data, list) and data and isinstance(data[0], dict):
        pairs = data[0].get("value") or []
    elif isinstance(data, dict):
        pairs = data.get("value") or data.get("segments") or []
    else:
        pairs = []

    out: List[Tuple[int, int]] = []
    for item in pairs:
        if isinstance(item, dict):
            start = _first_present(item, "start", "start_ms")
            end = _first_present(item, "end", "end_ms")
        else:
            start, end = item[0], item[1]
        out.append((int(start), int(end)))
    return sorted(out)


def _merge_vad_pairs(
    pairs: List[Tuple[int, int]],
    *,
    max_gap_ms: int,
    max_segment_ms: int,
    min_segment_ms: int,
) -> List[Tuple[int, int]]:
    merged: List[Tuple[int, int]] = []
    cur_s: Optional[int] = None
    cur_e: Optional[int] = None

    for start, end in pairs:
        if end <= start:
            continue
        if cur_s is None:
            cur_s, cur_e = start, end
            continue

        assert cur_e is not None
        can_merge = start - cur_e <= max_gap_ms and end - cur_s <= max_segment_ms
        if can_merge:
            cur_e = max(cur_e, end)
        else:
            if cur_e - cur_s >= min_segment_ms:
                merged.append((cur_s, cur_e))
            cur_s, cur_e = start, end

    if cur_s is not None and cur_e is not None and cur_e - cur_s >= min_segment_ms:
        merged.append((cur_s, cur_e))
    return merged


def _int_override(overrides: Dict[str, Any], key: str, default: int) -> int:
    value = overrides.get(key, overrides.get(key[4:], default) if key.startswith("glm_") else default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _str_override(overrides: Dict[str, Any], key: str, default: str) -> str:
    value = overrides.get(key, overrides.get(key[4:], default) if key.startswith("glm_") else default)
    return str(value) if value is not None else default


def _bool_override(overrides: Dict[str, Any], key: str, default: bool) -> bool:
    value = overrides.get(key, overrides.get(key[4:], default) if key.startswith("glm_") else default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _float_override(overrides: Dict[str, Any], key: str, default: float) -> float:
    value = overrides.get(key, overrides.get(key[4:], default) if key.startswith("glm_") else default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


class GLMASRModelManager:
    """GLM-ASR-Nano 模型缓存，与 FunASR 模型池完全隔离。"""

    _processor = None
    _model = None
    _vad_models: Dict[str, Any] = {}
    _load_lock = threading.Lock()
    _generate_lock = threading.Lock()
    _vad_lock = threading.Lock()

    @classmethod
    def _model_device(cls):
        model = cls._model
        device = getattr(model, "device", None)
        if device is not None:
            return device
        return next(model.parameters()).device

    @classmethod
    def _ensure_asr_model(cls):
        if cls._processor is not None and cls._model is not None:
            return cls._processor, cls._model

        with cls._load_lock:
            if cls._processor is not None and cls._model is not None:
                return cls._processor, cls._model

            from transformers import AutoModelForSeq2SeqLM, AutoProcessor

            cfg = get_settings().glm_asr
            cls._processor = AutoProcessor.from_pretrained(
                cfg.model, trust_remote_code=True
            )
            cls._model = AutoModelForSeq2SeqLM.from_pretrained(
                cfg.model,
                trust_remote_code=True,
                dtype="auto",
                device_map={"": cfg.device} if cfg.device.startswith("cuda") else "auto",
            )
            return cls._processor, cls._model

    @classmethod
    def _ensure_vad_model(cls):
        cfg = get_settings().glm_asr
        cache_key = f"{cfg.vad_model}:{cfg.device}"
        model = cls._vad_models.get(cache_key)
        if model is not None:
            return model

        with cls._load_lock:
            model = cls._vad_models.get(cache_key)
            if model is not None:
                return model

            from funasr import AutoModel

            model = AutoModel(
                model=cfg.vad_model,
                device=cfg.device,
                disable_update=True,
            )
            cls._vad_models[cache_key] = model
            return model

    @classmethod
    def preload_default_model(cls) -> None:
        cls._ensure_vad_model()
        cls._ensure_asr_model()

    @classmethod
    def run_vad(cls, audio_path: str) -> Tuple[Any, List[Tuple[int, int]], float]:
        model = cls._ensure_vad_model()
        started = time.perf_counter()
        with cls._vad_lock:
            raw = model.generate(input=audio_path, cache={}, is_final=True)
        return raw, _extract_vad_pairs(raw), time.perf_counter() - started

    @classmethod
    def transcribe_batch(
        cls,
        batch_paths: List[str],
        *,
        prompt: str,
        max_new_tokens: int,
        no_repeat_ngram_size: int,
        repetition_penalty: float,
        length_penalty: float,
    ) -> List[str]:
        if not batch_paths:
            return []

        import torch

        processor, model = cls._ensure_asr_model()
        with cls._generate_lock:
            inputs = processor.apply_transcription_request(
                batch_paths,
                prompt=[prompt] * len(batch_paths),
            )
            inputs = inputs.to(
                cls._model_device(),
                dtype=getattr(model, "dtype", torch.bfloat16),
            )
            with torch.inference_mode():
                outputs = model.generate(
                    **inputs,
                    do_sample=False,
                    max_new_tokens=max_new_tokens,
                    no_repeat_ngram_size=no_repeat_ngram_size,
                    repetition_penalty=repetition_penalty,
                    length_penalty=length_penalty,
                )
            new_tokens = outputs[:, inputs.input_ids.shape[1] :]
            return [
                text.strip()
                for text in processor.batch_decode(
                    new_tokens, skip_special_tokens=True
                )
            ]


class GLMASRTranscriber:
    def __init__(self, overrides: Optional[Dict[str, Any]] = None):
        self.settings = get_settings()
        self.cfg = self.settings.glm_asr
        self.overrides = overrides or {}

    def _make_clip(self, src: str, dst: str, start_ms: int, end_ms: int) -> None:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        cmd = [
            self.settings.media.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-to",
            f"{end_ms / 1000:.3f}",
            "-i",
            src,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            dst,
        ]
        subprocess.check_call(cmd)

    def _audio_duration_ms(self, audio_path: str) -> int:
        cmd = [
            self.settings.media.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ]
        output = subprocess.check_output(cmd, text=True).strip()
        return int(round(float(output) * 1000))

    @staticmethod
    def _recognition_bounds(
        start_ms: int,
        end_ms: int,
        *,
        audio_duration_ms: int,
        context_ms: int,
        min_recognition_ms: int,
    ) -> Tuple[int, int]:
        rec_start_ms = max(0, start_ms - context_ms)
        rec_end_ms = min(audio_duration_ms, end_ms + context_ms)
        if min_recognition_ms <= 0 or rec_end_ms - rec_start_ms >= min_recognition_ms:
            return rec_start_ms, rec_end_ms

        deficit = min_recognition_ms - (rec_end_ms - rec_start_ms)
        rec_start_ms = max(0, rec_start_ms - deficit // 2)
        rec_end_ms = min(audio_duration_ms, rec_end_ms + deficit - deficit // 2)
        if rec_end_ms - rec_start_ms < min_recognition_ms and rec_start_ms == 0:
            rec_end_ms = min(audio_duration_ms, min_recognition_ms)
        if rec_end_ms - rec_start_ms < min_recognition_ms and rec_end_ms == audio_duration_ms:
            rec_start_ms = max(0, audio_duration_ms - min_recognition_ms)
        return rec_start_ms, rec_end_ms

    def transcribe(self, audio_path: str, output_dir: str) -> GLMASRResult:
        started = time.perf_counter()
        vad_raw, vad_pairs, vad_elapsed_s = GLMASRModelManager.run_vad(audio_path)
        max_gap_ms = _int_override(
            self.overrides, "glm_max_gap_ms", self.cfg.max_gap_ms
        )
        max_segment_ms = _int_override(
            self.overrides, "glm_max_segment_ms", self.cfg.max_segment_ms
        )
        min_segment_ms = _int_override(
            self.overrides, "glm_min_segment_ms", self.cfg.min_segment_ms
        )
        batch_size = _int_override(
            self.overrides, "glm_batch_size", self.cfg.batch_size
        )
        max_new_tokens = _int_override(
            self.overrides, "glm_max_new_tokens", self.cfg.max_new_tokens
        )
        recognition_context_ms = _int_override(
            self.overrides,
            "glm_recognition_context_ms",
            self.cfg.recognition_context_ms,
        )
        min_recognition_ms = _int_override(
            self.overrides, "glm_min_recognition_ms", self.cfg.min_recognition_ms
        )
        no_repeat_ngram_size = _int_override(
            self.overrides, "glm_no_repeat_ngram_size", self.cfg.no_repeat_ngram_size
        )
        repetition_penalty = _float_override(
            self.overrides, "glm_repetition_penalty", self.cfg.repetition_penalty
        )
        length_penalty = _float_override(
            self.overrides, "glm_length_penalty", self.cfg.length_penalty
        )
        prompt = _str_override(self.overrides, "glm_prompt", self.cfg.prompt)
        keep_chunks = _bool_override(
            self.overrides, "glm_keep_chunks", self.cfg.keep_chunks
        )

        segments = _merge_vad_pairs(
            vad_pairs,
            max_gap_ms=max_gap_ms,
            max_segment_ms=max_segment_ms,
            min_segment_ms=min_segment_ms,
        )
        audio_duration_ms = self._audio_duration_ms(audio_path)
        chunks_dir = os.path.join(output_dir, "glm_vad_chunks")
        segment_specs: List[Tuple[int, int, int, int, int, str]] = []
        for idx, (start_ms, end_ms) in enumerate(segments, 1):
            rec_start_ms, rec_end_ms = self._recognition_bounds(
                start_ms,
                end_ms,
                audio_duration_ms=audio_duration_ms,
                context_ms=recognition_context_ms,
                min_recognition_ms=min_recognition_ms,
            )
            clip = os.path.join(
                chunks_dir,
                f"{idx:04d}_{start_ms:010d}_{end_ms:010d}_rec_{rec_start_ms:010d}_{rec_end_ms:010d}.wav",
            )
            self._make_clip(audio_path, clip, rec_start_ms, rec_end_ms)
            segment_specs.append((idx, start_ms, end_ms, rec_start_ms, rec_end_ms, clip))

        sentence_info: List[Dict[str, Any]] = []
        texts: List[str] = []
        for batch_start in range(0, len(segment_specs), batch_size):
            batch_specs = segment_specs[batch_start : batch_start + batch_size]
            batch_paths = [spec[5] for spec in batch_specs]
            decoded = GLMASRModelManager.transcribe_batch(
                batch_paths,
                prompt=prompt,
                max_new_tokens=max_new_tokens,
                no_repeat_ngram_size=no_repeat_ngram_size,
                repetition_penalty=repetition_penalty,
                length_penalty=length_penalty,
            )
            for (_, start_ms, end_ms, _, _, _), text in zip(batch_specs, decoded):
                if not text:
                    continue
                texts.append(text)
                sentence_info.append(
                    {
                        "text": text,
                        "timestamp": [[start_ms, end_ms]],
                    }
                )

        srt = generate_srt(sentence_info) if sentence_info else ""
        text = "\n".join(texts)
        meta = {
            "provider": "glm",
            "model": self.cfg.model,
            "vad_model": self.cfg.vad_model,
            "raw_vad_segments": len(vad_pairs),
            "merged_vad_segments": len(segment_specs),
            "nonempty_segments": len(sentence_info),
            "max_new_tokens": max_new_tokens,
            "recognition_context_ms": recognition_context_ms,
            "min_recognition_ms": min_recognition_ms,
            "no_repeat_ngram_size": no_repeat_ngram_size,
            "repetition_penalty": repetition_penalty,
            "length_penalty": length_penalty,
            "vad_elapsed_s": round(vad_elapsed_s, 3),
            "asr_elapsed_s": round(time.perf_counter() - started - vad_elapsed_s, 3),
            "total_elapsed_s": round(time.perf_counter() - started, 3),
            "has_word_timestamp": False,
            "timestamp_granularity": "VAD segment boundary",
        }

        if not keep_chunks:
            for _, _, _, _, _, clip in segment_specs:
                try:
                    os.remove(clip)
                except OSError:
                    pass
            shutil.rmtree(chunks_dir, ignore_errors=True)

        return GLMASRResult(
            text=text,
            srt=srt,
            sentence_info=sentence_info,
            meta=meta,
        )
