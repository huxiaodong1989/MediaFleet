#!/usr/bin/env python3
"""
Benchmark FunASR AutoModel concurrency on one machine/GPU.

Examples:
  python scripts/benchmark_funasr_automodel_concurrency.py --audio /data/test.wav --device cuda:0 --concurrency 1,2,4,8
  python scripts/benchmark_funasr_automodel_concurrency.py --audio /data/test.wav --mode both --pool-size auto
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import queue
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class GpuSample:
    timestamp: float
    util_gpu: float | None
    util_mem: float | None
    mem_used_mb: float | None
    mem_total_mb: float | None
    power_w: float | None


@dataclass
class RunResult:
    mode: str
    concurrency: int
    pool_size: int
    total_requests: int
    ok: int
    errors: int
    wall_s: float
    avg_latency_s: float | None
    p50_latency_s: float | None
    p95_latency_s: float | None
    max_latency_s: float | None
    requests_per_min: float
    audio_minutes_per_hour: float | None
    speedup_vs_c1: float | None
    efficiency_vs_c1: float | None
    gpu_util_avg: float | None
    gpu_util_max: float | None
    gpu_mem_used_avg_mb: float | None
    gpu_mem_used_max_mb: float | None
    first_error: str | None


class GpuMonitor:
    def __init__(self, interval_s: float = 1.0):
        self.interval_s = interval_s
        self.samples: list[GpuSample] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.enabled = shutil.which("nvidia-smi") is not None

    def __enter__(self) -> "GpuMonitor":
        if self.enabled:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_s + 2)

    def _run(self) -> None:
        fields = [
            "utilization.gpu",
            "utilization.memory",
            "memory.used",
            "memory.total",
            "power.draw",
        ]
        cmd = [
            "nvidia-smi",
            f"--query-gpu={','.join(fields)}",
            "--format=csv,noheader,nounits",
        ]
        while not self._stop.is_set():
            sample = self._query_once(cmd)
            if sample:
                self.samples.append(sample)
            self._stop.wait(self.interval_s)

    @staticmethod
    def _num(value: str) -> float | None:
        value = value.strip()
        if value in {"", "[N/A]", "N/A"}:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _query_once(self, cmd: list[str]) -> GpuSample | None:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if proc.returncode != 0:
                return None
            first_line = proc.stdout.strip().splitlines()[0]
            cols = [self._num(item) for item in first_line.split(",")]
            while len(cols) < 5:
                cols.append(None)
            return GpuSample(time.time(), cols[0], cols[1], cols[2], cols[3], cols[4])
        except Exception:
            return None


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


def mean(values: list[float | None]) -> float | None:
    filtered = [v for v in values if v is not None]
    if not filtered:
        return None
    return statistics.fmean(filtered)


def max_or_none(values: list[float | None]) -> float | None:
    filtered = [v for v in values if v is not None]
    if not filtered:
        return None
    return max(filtered)


def parse_concurrency(value: str) -> list[int]:
    result: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        result.append(int(part))
    if not result or any(item < 1 for item in result):
        raise argparse.ArgumentTypeError("--concurrency must contain positive integers")
    return result


def audio_duration_s(audio_path: Path) -> float | None:
    try:
        import soundfile as sf

        info = sf.info(str(audio_path))
        return float(info.frames) / float(info.samplerate)
    except Exception:
        return None


def normalize_audio(input_path: Path, ffmpeg: str) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    suffix = input_path.suffix.lower()
    if suffix == ".wav":
        return input_path, None

    tmpdir = tempfile.TemporaryDirectory(prefix="funasr_bench_")
    output_path = Path(tmpdir.name) / "input_16k_mono.wav"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tmpdir.cleanup()
        raise RuntimeError(f"ffmpeg normalize failed: {proc.stderr[-2000:]}")
    return output_path, tmpdir


def build_model(args: argparse.Namespace):
    from funasr import AutoModel

    vad_kwargs = {"max_single_segment_time": args.vad_max_segment_ms}
    if args.model_type == "sensevoice":
        return AutoModel(
            model=args.sensevoice_model,
            trust_remote_code=args.sensevoice_trust_remote_code,
            vad_model="fsmn-vad",
            vad_kwargs=vad_kwargs,
            device=args.device,
            ncpu=args.ncpu,
            disable_update=args.disable_update,
        )

    model_name = args.paraformer_en_model if args.lang == "en" else args.paraformer_zh_model
    return AutoModel(
        model=model_name,
        vad_model=args.vad_model,
        vad_kwargs=vad_kwargs,
        punc_model=args.punc_model,
        spk_model=args.spk_model,
        device=args.device,
        ncpu=args.ncpu,
        disable_update=args.disable_update,
    )


def generate_once(model: Any, audio_path: Path, args: argparse.Namespace) -> Any:
    if args.model_type == "sensevoice":
        return model.generate(
            input=str(audio_path),
            cache={},
            language=args.sensevoice_language if args.sensevoice_language else args.lang,
            use_itn=args.sensevoice_use_itn,
            batch_size_s=args.batch_size_s,
            merge_vad=args.merge_vad,
            merge_length_s=args.merge_length_s,
            output_timestamp=args.sensevoice_output_timestamp,
        )

    return model.generate(
        input=str(audio_path),
        cache={},
        return_spk_res=False,
        sentence_timestamp=True,
        return_raw_text=True,
        is_final=True,
        hotword=args.hotword,
        batch_size_s=args.batch_size_s,
        pred_timestamp=args.lang == "en",
        en_post_proc=True,
    )


def warmup(models: list[Any], audio_path: Path, args: argparse.Namespace) -> None:
    if args.warmup <= 0:
        return
    print(f"[warmup] runs={args.warmup}, models={len(models)}", flush=True)
    for idx in range(args.warmup):
        model = models[idx % len(models)]
        start = time.perf_counter()
        generate_once(model, audio_path, args)
        print(f"[warmup] {idx + 1}/{args.warmup}: {time.perf_counter() - start:.2f}s", flush=True)


def run_level(
    mode: str,
    concurrency: int,
    models: list[Any],
    audio_path: Path,
    audio_minutes: float | None,
    args: argparse.Namespace,
    baseline_wall_s: float | None,
) -> RunResult:
    total_requests = args.requests if args.requests else max(concurrency * args.requests_per_worker, concurrency)
    model_queue: queue.Queue[Any] | None = None

    if mode == "pool":
        model_queue = queue.Queue()
        for model in models:
            model_queue.put(model)

    latencies: list[float] = []
    errors: list[str] = []

    def task(task_idx: int) -> float:
        model = models[0]
        if model_queue is not None:
            model = model_queue.get()
        try:
            started = time.perf_counter()
            generate_once(model, audio_path, args)
            return time.perf_counter() - started
        finally:
            if model_queue is not None:
                model_queue.put(model)

    print(
        f"[run] mode={mode} concurrency={concurrency} pool_size={len(models)} requests={total_requests}",
        flush=True,
    )
    wall_started = time.perf_counter()
    with GpuMonitor(args.gpu_sample_interval) as gpu:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(task, idx) for idx in range(total_requests)]
            for future in as_completed(futures):
                try:
                    latencies.append(float(future.result()))
                except Exception as exc:
                    errors.append(repr(exc))
    wall_s = time.perf_counter() - wall_started

    ok = len(latencies)
    requests_per_min = ok / wall_s * 60 if wall_s > 0 else 0.0
    audio_minutes_per_hour = None
    if audio_minutes is not None:
        audio_minutes_per_hour = ok * audio_minutes / wall_s * 3600

    speedup = None
    efficiency = None
    if baseline_wall_s and wall_s > 0:
        comparable_serial_wall = baseline_wall_s * total_requests
        speedup = comparable_serial_wall / wall_s
        efficiency = speedup / concurrency

    return RunResult(
        mode=mode,
        concurrency=concurrency,
        pool_size=len(models),
        total_requests=total_requests,
        ok=ok,
        errors=len(errors),
        wall_s=wall_s,
        avg_latency_s=mean(latencies),
        p50_latency_s=percentile(latencies, 50),
        p95_latency_s=percentile(latencies, 95),
        max_latency_s=max(latencies) if latencies else None,
        requests_per_min=requests_per_min,
        audio_minutes_per_hour=audio_minutes_per_hour,
        speedup_vs_c1=speedup,
        efficiency_vs_c1=efficiency,
        gpu_util_avg=mean([sample.util_gpu for sample in gpu.samples]),
        gpu_util_max=max_or_none([sample.util_gpu for sample in gpu.samples]),
        gpu_mem_used_avg_mb=mean([sample.mem_used_mb for sample in gpu.samples]),
        gpu_mem_used_max_mb=max_or_none([sample.mem_used_mb for sample in gpu.samples]),
        first_error=errors[0] if errors else None,
    )


def print_result(result: RunResult) -> None:
    audio_tput = "-"
    if result.audio_minutes_per_hour is not None:
        audio_tput = f"{result.audio_minutes_per_hour:.1f}"
    speedup = "-" if result.speedup_vs_c1 is None else f"{result.speedup_vs_c1:.2f}"
    efficiency = "-" if result.efficiency_vs_c1 is None else f"{result.efficiency_vs_c1:.2f}"
    gpu_avg = "-" if result.gpu_util_avg is None else f"{result.gpu_util_avg:.1f}%"
    mem_max = "-" if result.gpu_mem_used_max_mb is None else f"{result.gpu_mem_used_max_mb:.0f}MB"
    p95 = "-" if result.p95_latency_s is None else f"{result.p95_latency_s:.2f}s"
    print(
        "[result] "
        f"mode={result.mode} c={result.concurrency} pool={result.pool_size} "
        f"ok={result.ok}/{result.total_requests} err={result.errors} wall={result.wall_s:.2f}s "
        f"rpm={result.requests_per_min:.2f} audio_min/h={audio_tput} "
        f"p95={p95} speedup={speedup} eff={efficiency} gpu_avg={gpu_avg} mem_max={mem_max}",
        flush=True,
    )
    if result.first_error:
        print(f"[error] first_error={result.first_error}", flush=True)


def save_results(results: list[RunResult], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".json":
        output.write_text(
            json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return

    with output.open("w", encoding="utf-8", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=list(asdict(results[0]).keys()))
        writer.writeheader()
        for item in results:
            writer.writerow(asdict(item))


def recommend(results: list[RunResult], min_efficiency: float) -> RunResult | None:
    healthy = [item for item in results if item.errors == 0 and item.ok == item.total_requests]
    if not healthy:
        return None
    efficient = [
        item
        for item in healthy
        if item.efficiency_vs_c1 is None or item.efficiency_vs_c1 >= min_efficiency
    ]
    candidates = efficient or healthy
    return max(candidates, key=lambda item: (item.audio_minutes_per_hour or 0.0, item.requests_per_min))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark FunASR AutoModel concurrency")
    parser.add_argument("--audio", required=True, help="Input audio/video path. Non-wav files are normalized with ffmpeg.")
    parser.add_argument("--mode", choices=["shared", "pool", "both"], default="both")
    parser.add_argument("--concurrency", type=parse_concurrency, default=parse_concurrency("1,2,4,8,16,32"))
    parser.add_argument("--requests", type=int, default=0, help="Requests per level. Default: concurrency * requests-per-worker.")
    parser.add_argument("--requests-per-worker", type=int, default=2)
    parser.add_argument("--pool-size", default="auto", help="'auto' means pool size equals concurrency in pool mode.")
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--output", default="output/funasr_automodel_benchmark.csv")
    parser.add_argument("--min-efficiency", type=float, default=0.6)
    parser.add_argument("--gpu-sample-interval", type=float, default=1.0)
    parser.add_argument("--ffmpeg", default=os.environ.get("MEDIA_FFMPEG_PATH", "ffmpeg"))

    parser.add_argument("--model-type", choices=["paraformer", "sensevoice"], default=os.environ.get("FUNASR_MODEL_TYPE", "paraformer"))
    parser.add_argument("--device", default=os.environ.get("FUNASR_DEVICE", "cuda:0"))
    parser.add_argument("--lang", default="zh")
    parser.add_argument("--ncpu", type=int, default=int(os.environ.get("FUNASR_NCPU", "4")))
    parser.add_argument("--disable-update", action=argparse.BooleanOptionalAction, default=os.environ.get("FUNASR_DISABLE_UPDATE", "true").lower() == "true")
    parser.add_argument("--batch-size-s", type=int, default=int(os.environ.get("FUNASR_BATCH_SIZE_S", "60")))
    parser.add_argument("--merge-vad", action=argparse.BooleanOptionalAction, default=os.environ.get("FUNASR_MERGE_VAD", "true").lower() == "true")
    parser.add_argument("--merge-length-s", type=int, default=int(os.environ.get("FUNASR_MERGE_LENGTH_S", "15")))
    parser.add_argument("--vad-max-segment-ms", type=int, default=int(os.environ.get("FUNASR_VAD_MAX_SEGMENT_MS", "30000")))
    parser.add_argument("--hotword", default="")

    parser.add_argument("--paraformer-zh-model", default=os.environ.get("FUNASR_PARAFORMER_ZH_MODEL", "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"))
    parser.add_argument("--paraformer-en-model", default=os.environ.get("FUNASR_PARAFORMER_EN_MODEL", "iic/speech_paraformer_asr-en-16k-vocab4199-pytorch"))
    parser.add_argument("--vad-model", default=os.environ.get("FUNASR_VAD_MODEL", "damo/speech_fsmn_vad_zh-cn-16k-common-pytorch"))
    parser.add_argument("--punc-model", default=os.environ.get("FUNASR_PUNC_MODEL", "damo/punc_ct-transformer_zh-cn-common-vocab272727-pytorch"))
    parser.add_argument("--spk-model", default=os.environ.get("FUNASR_SPK_MODEL", "damo/speech_campplus_sv_zh-cn_16k-common"))

    parser.add_argument("--sensevoice-model", default=os.environ.get("FUNASR_SENSEVOICE_MODEL", "iic/SenseVoiceSmall"))
    parser.add_argument("--sensevoice-language", default=os.environ.get("FUNASR_SENSEVOICE_LANGUAGE", "auto"))
    parser.add_argument("--sensevoice-use-itn", action=argparse.BooleanOptionalAction, default=os.environ.get("FUNASR_SENSEVOICE_USE_ITN", "true").lower() == "true")
    parser.add_argument("--sensevoice-output-timestamp", action=argparse.BooleanOptionalAction, default=os.environ.get("FUNASR_SENSEVOICE_OUTPUT_TIMESTAMP", "true").lower() == "true")
    parser.add_argument("--sensevoice-trust-remote-code", action=argparse.BooleanOptionalAction, default=os.environ.get("FUNASR_SENSEVOICE_TRUST_REMOTE_CODE", "true").lower() == "true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.requests < 0:
        raise ValueError("--requests must be >= 0")
    if args.requests_per_worker < 1:
        raise ValueError("--requests-per-worker must be >= 1")

    input_path = Path(args.audio).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    audio_path, tmpdir = normalize_audio(input_path, args.ffmpeg)
    try:
        duration = audio_duration_s(audio_path)
        audio_minutes = duration / 60 if duration else None
        if duration:
            print(f"[audio] path={audio_path} duration={duration:.2f}s", flush=True)
        else:
            print(f"[audio] path={audio_path} duration=unknown", flush=True)

        modes = ["shared", "pool"] if args.mode == "both" else [args.mode]
        results: list[RunResult] = []
        baselines: dict[str, float] = {}

        for mode in modes:
            for concurrency in args.concurrency:
                pool_size = 1
                if mode == "pool":
                    pool_size = concurrency if args.pool_size == "auto" else int(args.pool_size)
                    pool_size = max(1, min(pool_size, concurrency))

                print(f"[model] building mode={mode} concurrency={concurrency} pool_size={pool_size}", flush=True)
                models = [build_model(args) for _ in range(pool_size)]
                warmup(models, audio_path, args)

                baseline = baselines.get(mode)
                result = run_level(mode, concurrency, models, audio_path, audio_minutes, args, baseline)
                if concurrency == 1 and result.errors == 0 and result.ok > 0:
                    baselines[mode] = result.wall_s / result.ok
                    result.speedup_vs_c1 = 1.0
                    result.efficiency_vs_c1 = 1.0

                results.append(result)
                print_result(result)

                del models
                try:
                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass

        if results:
            output = Path(args.output)
            save_results(results, output)
            print(f"[output] saved={output.resolve()}", flush=True)

            best = recommend(results, args.min_efficiency)
            if best:
                print(
                    "[recommend] "
                    f"mode={best.mode} concurrency={best.concurrency} pool_size={best.pool_size} "
                    f"audio_min/h={best.audio_minutes_per_hour or 0:.1f} "
                    f"efficiency={best.efficiency_vs_c1 if best.efficiency_vs_c1 is not None else 0:.2f}",
                    flush=True,
                )
            else:
                print("[recommend] no healthy level completed without errors", flush=True)

        return 0
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
