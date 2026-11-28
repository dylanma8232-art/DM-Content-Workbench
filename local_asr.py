"""Local MLX Whisper adapter. Requires mlx-whisper and ffmpeg at runtime."""

import gc
import importlib
import json
import os
from pathlib import Path
import sys
import threading

DEFAULT_MODEL = "mlx-community/whisper-small-mlx"
MAX_AUDIO_BYTES = 25 * 1024 * 1024
_LOCK = threading.RLock()


def _complete_snapshot(path: Path) -> bool:
    try:
        config = json.loads((path / "config.json").read_text())
        return isinstance(config, dict) and bool(config) and any(
            (path / name).is_file() and (path / name).stat().st_size > 0
            for name in ("weights.safetensors", "weights.npz")
        )
    except (OSError, ValueError):
        return False


def _model_source(model_id: str) -> str:
    """Prefer complete local Hub snapshots, without contacting Hugging Face."""
    hf_home = Path(os.environ.get(
        "HF_HOME", str(Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "huggingface")
    )).expanduser()
    cache = Path(os.environ.get("HF_HUB_CACHE", str(hf_home / "hub"))).expanduser()
    repo = cache / ("models--" + model_id.replace("/", "--"))
    snapshots = repo / "snapshots"
    try:
        revision = (repo / "refs" / "main").read_text().strip()
        preferred = snapshots / revision
        if revision and preferred.parent == snapshots and _complete_snapshot(preferred):
            return str(preferred.resolve())
    except OSError:
        pass
    if snapshots.is_dir():
        for candidate in sorted(snapshots.iterdir(), reverse=True):
            if candidate.is_dir() and _complete_snapshot(candidate):
                return str(candidate.resolve())
    return model_id


def transcribe(path: str, model_id: str = DEFAULT_MODEL) -> dict:
    """Transcribe a local file of at most 25 MiB, detecting its language.

    Symlinks are resolved before validation. Decode/model errors propagate;
    this adapter never substitutes text when inference fails. A complete Hub
    cache is used offline; otherwise mlx-whisper resolves the requested repo.
    """
    audio = Path(path).expanduser().resolve(strict=True)
    if not audio.is_file():
        raise ValueError("Audio path must be a local regular file")
    size = audio.stat().st_size
    if not size:
        raise ValueError("Audio file is empty")
    if size > MAX_AUDIO_BYTES:
        raise ValueError("Audio file exceeds the 25 MiB limit")
    with _LOCK:
        try:
            whisper = importlib.import_module("mlx_whisper")
        except ModuleNotFoundError as exc:
            raise RuntimeError("Local transcription requires mlx-whisper in the active Python environment") from exc
        result = whisper.transcribe(
            str(audio), path_or_hf_repo=_model_source(model_id),
            language=None, task="transcribe", verbose=None,
        )
        return {
            "text": result["text"],
            "segments": [
                {"start": float(segment["start"]), "end": float(segment["end"]),
                 "text": segment["text"]}
                for segment in result["segments"]
            ],
            "language": result["language"],
        }


def unload() -> None:
    """Release Whisper's cached model and MLX allocator cache.

    Safe before first use and on repeated calls. The service must serialize
    ASR/VLM switches; this lock only coordinates this adapter's own calls.
    """
    with _LOCK:
        module = sys.modules.get("mlx_whisper.transcribe")
        holder = getattr(module, "ModelHolder", None)
        mx = sys.modules.get("mlx.core")
        if mx is not None:
            synchronize = getattr(mx, "synchronize", None)
            if callable(synchronize):
                synchronize()
        if holder is not None:
            holder.model = None
            holder.model_path = None
        gc.collect()
        if mx is not None:
            clear_cache = getattr(mx, "clear_cache", None)
            if not callable(clear_cache):
                clear_cache = getattr(getattr(mx, "metal", None), "clear_cache", None)
            if callable(clear_cache):
                clear_cache()
