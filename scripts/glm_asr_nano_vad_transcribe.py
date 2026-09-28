#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any


def run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True).strip()


def duration_s(path: Path) -> float:
    return float(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ]
        )
    )


def srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)


def quality_flags(text: str, speech_duration_s: float) -> list[str]:
    flags: list[str] = []
    stripped = text.strip()
    if not stripped:
        return flags
    if stripped in {"#", "＃"}:
        flags.append("noise_token")
    for unit_len in range(1, 9):
        for i in range(0, max(0, len(stripped) - unit_len * 8 + 1)):
            unit = stripped[i : i + unit_len]
            if unit and unit * 8 in stripped:
                flags.append("repetition")
                return sorted(set(flags))
    if speech_duration_s > 0 and len(stripped) / (speech_duration_s / 60) > 800:
        flags.append("speed_outlier")
    return sorted(set(flags))


def extract_vad_pairs(data: Any) -> list[tuple[int, int]]:
    if isinstance(data, list) and data and isinstance(data[0], dict):
        pairs = data[0].get("value") or []
    elif isinstance(data, dict):
        pairs = data.get("value") or data.get("segments") or []
    else:
        pairs = []

    out: list[tuple[int, int]] = []
    for item in pairs:
        if isinstance(item, dict):
            start = item.get("start") or item.get("start_ms")
            end = item.get("end") or item.get("end_ms")
        else:
            start, end = item[0], item[1]
        out.append((int(start), int(end)))
    return sorted(out)


def merge_vad_pairs(
    pairs: list[tuple[int, int]],
    *,
    max_gap_ms: int,
    max_segment_ms: int,
    min_segment_ms: int,
    max_duration_ms: int | None,
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    cur_s: int | None = None
    cur_e: int | None = None

    for start, end in pairs:
        if max_duration_ms is not None and start >= max_duration_ms:
            break
        if max_duration_ms is not None:
            end = min(end, max_duration_ms)
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


def make_clip(src: Path, dst: Path, start_ms: int, end_ms: int) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-to",
            f"{end_ms / 1000:.3f}",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            str(dst),
        ]
    )


def load_or_run_vad(audio: Path, vad_json: Path | None, vad_model: str, device: str) -> tuple[Any, list[tuple[int, int]], float]:
    if vad_json is not None:
        data = json.loads(vad_json.read_text(encoding="utf-8"))
        return data, extract_vad_pairs(data), 0.0

    from funasr import AutoModel

    started = time.time()
    model = AutoModel(model=vad_model, device=device, disable_update=True)
    data = model.generate(input=str(audio), cache={}, is_final=True)
    return data, extract_vad_pairs(data), time.time() - started


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Transcribe long audio with GLM-ASR-Nano on VAD speech segments."
    )
    parser.add_argument("--audio", action="append", required=True, help="Audio path. Can be passed multiple times.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model", default="/root/.cache/modelscope/hub/models/ZhipuAI/GLM-ASR-Nano-2512")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--vad-model", default="fsmn-vad")
    parser.add_argument("--vad-json", action="append", help="Optional existing VAD json, aligned by --audio order.")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-duration-s", type=float, default=0.0, help="0 means full audio; useful for quick tests.")
    parser.add_argument("--max-gap-ms", type=int, default=700, help="Merge nearby VAD segments separated by this gap.")
    parser.add_argument("--max-segment-ms", type=int, default=30000)
    parser.add_argument("--min-segment-ms", type=int, default=800)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--prompt", default="请将音频转写成简体中文文本。")
    parser.add_argument("--keep-chunks", action="store_true", help="Keep temporary 16k wav VAD chunks.")
    args = parser.parse_args()

    if args.vad_json and len(args.vad_json) not in {1, len(args.audio)}:
        raise SystemExit("--vad-json must be passed once or the same number of times as --audio")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    audio_jobs: list[dict[str, Any]] = []
    vad_total_elapsed_s = 0.0
    for audio_index, audio_s in enumerate(args.audio):
        audio = Path(audio_s)
        audio_dir = out_dir / audio.stem
        audio_dir.mkdir(parents=True, exist_ok=True)
        max_duration_ms = int(args.max_duration_s * 1000) if args.max_duration_s > 0 else None
        vad_json = None
        if args.vad_json:
            vad_json = Path(args.vad_json[0 if len(args.vad_json) == 1 else audio_index])
        vad_raw, vad_pairs, vad_elapsed_s = load_or_run_vad(audio, vad_json, args.vad_model, args.device)
        vad_total_elapsed_s += vad_elapsed_s
        (audio_dir / "vad_raw.json").write_text(json.dumps(vad_raw, ensure_ascii=False, indent=2), encoding="utf-8")
        audio_jobs.append(
            {
                "audio": audio,
                "audio_dir": audio_dir,
                "vad_pairs": vad_pairs,
                "vad_elapsed_s": vad_elapsed_s,
                "segments": merge_vad_pairs(
                    vad_pairs,
                    max_gap_ms=args.max_gap_ms,
                    max_segment_ms=args.max_segment_ms,
                    min_segment_ms=args.min_segment_ms,
                    max_duration_ms=max_duration_ms,
                ),
            }
        )

    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoProcessor

    init_started = time.time()
    processor = AutoProcessor.from_pretrained(args.model, trust_remote_code=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(
        args.model,
        trust_remote_code=True,
        dtype="auto",
        device_map={"": args.device} if args.device.startswith("cuda") else "auto",
    )
    init_elapsed_s = time.time() - init_started

    summary_rows: list[dict[str, object]] = []
    for job in audio_jobs:
        audio = job["audio"]
        audio_dir = job["audio_dir"]
        chunks_dir = audio_dir / "vad_chunks"

        audio_duration = duration_s(audio)
        vad_pairs = job["vad_pairs"]
        vad_elapsed_s = job["vad_elapsed_s"]
        segments = job["segments"]

        segment_specs: list[tuple[int, int, int, Path]] = []
        for idx, (start_ms, end_ms) in enumerate(segments, 1):
            clip = chunks_dir / f"{idx:04d}_{start_ms:010d}_{end_ms:010d}.wav"
            make_clip(audio, clip, start_ms, end_ms)
            segment_specs.append((idx, start_ms, end_ms, clip))

        started = time.time()
        segment_rows: list[dict[str, object]] = []
        texts: list[str] = []
        srt_blocks: list[str] = []

        for batch_start in range(0, len(segment_specs), args.batch_size):
            batch_specs = segment_specs[batch_start : batch_start + args.batch_size]
            batch_paths = [str(spec[3]) for spec in batch_specs]
            batch_started = time.time()
            status = "ok"
            error = ""
            decoded: list[str] = []
            try:
                inputs = processor.apply_transcription_request(
                    batch_paths,
                    prompt=[args.prompt] * len(batch_paths),
                )
                inputs = inputs.to(model.device, dtype=getattr(model, "dtype", torch.bfloat16))
                with torch.inference_mode():
                    outputs = model.generate(**inputs, do_sample=False, max_new_tokens=args.max_new_tokens)
                new_tokens = outputs[:, inputs.input_ids.shape[1] :]
                decoded = [text.strip() for text in processor.batch_decode(new_tokens, skip_special_tokens=True)]
            except Exception as exc:
                status = "failed"
                error = str(exc)
                decoded = [""] * len(batch_specs)
                (audio_dir / f"batch_{batch_start // args.batch_size + 1:04d}_error.txt").write_text(
                    traceback.format_exc(), encoding="utf-8"
                )

            batch_elapsed_s = time.time() - batch_started
            for (idx, start_ms, end_ms, clip), text in zip(batch_specs, decoded):
                speech_duration = (end_ms - start_ms) / 1000
                flags = quality_flags(text, speech_duration)
                if text:
                    texts.append(text)
                    srt_blocks.append(
                        f"{len(srt_blocks) + 1}\n"
                        f"{srt_time(start_ms / 1000)} --> {srt_time(end_ms / 1000)}\n"
                        f"{text}\n"
                    )
                segment_rows.append(
                    {
                        "audio": audio.name,
                        "segment": idx,
                        "start_s": round(start_ms / 1000, 3),
                        "end_s": round(end_ms / 1000, 3),
                        "speech_duration_s": round(speech_duration, 3),
                        "batch_size": len(batch_specs),
                        "batch_elapsed_s": round(batch_elapsed_s, 3),
                        "status": status,
                        "text_chars": len(text),
                        "chars_per_min_speech": round(len(text) / ((end_ms - start_ms) / 60000), 2)
                        if end_ms > start_ms and text
                        else "",
                        "quality_flags": "|".join(flags),
                        "text": text,
                        "error": error.replace("\n", " ")[:300],
                    }
                )

        elapsed_s = time.time() - started
        full_text = "\n".join(texts)
        speech_duration_s = sum((end_ms - start_ms) / 1000 for _, start_ms, end_ms, _ in segment_specs)
        wall_duration_s = min(audio_duration, args.max_duration_s) if args.max_duration_s > 0 else audio_duration

        (audio_dir / "text.txt").write_text(full_text, encoding="utf-8")
        (audio_dir / "result.vad.srt").write_text("\n".join(srt_blocks), encoding="utf-8")
        (audio_dir / "segments.json").write_text(json.dumps(segment_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        write_csv(audio_dir / "segments.csv", segment_rows)

        summary = {
            "audio": audio.name,
            "audio_duration_s": round(audio_duration, 3),
            "transcribed_wall_duration_s": round(wall_duration_s, 3),
            "speech_duration_s": round(speech_duration_s, 3),
            "raw_vad_segments": len(vad_pairs),
            "merged_vad_segments": len(segment_specs),
            "max_gap_ms": args.max_gap_ms,
            "max_segment_ms": args.max_segment_ms,
            "min_segment_ms": args.min_segment_ms,
            "batch_size": args.batch_size,
            "vad_elapsed_s": round(vad_elapsed_s, 3),
            "asr_elapsed_s": round(elapsed_s, 3),
            "total_elapsed_s": round(vad_elapsed_s + elapsed_s, 3),
            "asr_rtf_wall": round(elapsed_s / wall_duration_s, 5) if wall_duration_s else "",
            "asr_rtf_speech": round(elapsed_s / speech_duration_s, 5) if speech_duration_s else "",
            "audio_min_per_h_wall": round((wall_duration_s / 60) / (elapsed_s / 3600), 2) if elapsed_s else "",
            "speech_min_per_h": round((speech_duration_s / 60) / (elapsed_s / 3600), 2) if elapsed_s else "",
            "nonempty_segments": sum(1 for row in segment_rows if row["text"]),
            "flagged_segments": sum(1 for row in segment_rows if row["quality_flags"]),
            "text_chars": len(full_text),
            "chars_per_min_wall": round(len(full_text) / (wall_duration_s / 60), 2) if wall_duration_s else "",
            "chars_per_min_speech": round(len(full_text) / (speech_duration_s / 60), 2) if speech_duration_s else "",
            "has_word_timestamp": False,
            "timestamp_granularity": "VAD segment boundary",
            "srt_created": True,
            "note": "GLM-ASR-Nano does not return word timestamps here; SRT uses VAD speech-segment boundaries.",
        }
        summary_rows.append(summary)
        (audio_dir / "meta.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        if not args.keep_chunks:
            for _, _, _, clip in segment_specs:
                clip.unlink(missing_ok=True)
            try:
                chunks_dir.rmdir()
            except OSError:
                pass

    write_csv(out_dir / "summary.csv", summary_rows)
    (out_dir / "summary.json").write_text(
        json.dumps(
            {
                "init_elapsed_s": round(init_elapsed_s, 3),
                "vad_total_elapsed_s": round(vad_total_elapsed_s, 3),
                "model": args.model,
                "device": args.device,
                "vad_model": args.vad_model,
                "results": summary_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
