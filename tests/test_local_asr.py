import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import local_asr


class LocalASRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root / "speech.wav"
        self.audio.write_bytes(b"test audio; decoder is mocked")
        self.cache = self.root / "hub"
        self.env = patch.dict("os.environ", {"HF_HUB_CACHE": str(self.cache)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.whisper = types.ModuleType("mlx_whisper")
        self.whisper.transcribe = Mock(return_value={
            "text": " Hello.", "language": "en",
            "segments": [{"start": 0, "end": 1.5, "text": " Hello.", "tokens": [1]}],
        })
        self.modules = patch.dict(sys.modules, {"mlx_whisper": self.whisper})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def snapshot(self, revision="complete", model=local_asr.DEFAULT_MODEL):
        repo = self.cache / ("models--" + model.replace("/", "--"))
        snapshot = repo / "snapshots" / revision
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text(json.dumps({"n_mels": 80}))
        (snapshot / "weights.npz").write_bytes(b"weights")
        return repo, snapshot

    def test_realpath_and_normalized_response_and_auto_language(self):
        link = self.root / "link.wav"
        link.symlink_to(self.audio)
        result = local_asr.transcribe(str(link))
        self.assertEqual(result, {
            "text": " Hello.", "language": "en",
            "segments": [{"start": 0.0, "end": 1.5, "text": " Hello."}],
        })
        self.whisper.transcribe.assert_called_once_with(
            str(self.audio.resolve()), path_or_hf_repo=local_asr.DEFAULT_MODEL,
            language=None, task="transcribe", verbose=None,
        )

    def test_rejects_missing_directory_empty_and_remote_paths_before_import(self):
        empty = self.root / "empty.wav"
        empty.touch()
        for path, error in [
            (self.root / "missing", FileNotFoundError),
            (self.root, ValueError), (empty, ValueError),
            ("https://example.com/audio.wav", FileNotFoundError),
        ]:
            with self.subTest(path=path), patch.object(local_asr.importlib, "import_module") as load:
                with self.assertRaises(error):
                    local_asr.transcribe(str(path))
                load.assert_not_called()

    def test_size_limit_inclusive(self):
        with self.audio.open("wb") as audio:
            audio.truncate(local_asr.MAX_AUDIO_BYTES)
        local_asr.transcribe(str(self.audio))
        with self.audio.open("ab") as audio:
            audio.write(b"x")
        with self.assertRaisesRegex(ValueError, "25 MiB"):
            local_asr.transcribe(str(self.audio))
        self.assertEqual(self.whisper.transcribe.call_count, 1)

    def test_complete_main_snapshot_preferred(self):
        repo, snapshot = self.snapshot()
        self.snapshot("zzz-other")
        (repo / "refs").mkdir()
        (repo / "refs" / "main").write_text("complete\n")
        local_asr.transcribe(str(self.audio))
        self.assertEqual(self.whisper.transcribe.call_args.kwargs["path_or_hf_repo"], str(snapshot.resolve()))

    def test_snapshot_without_ref_and_safetensors_supported(self):
        _, snapshot = self.snapshot(model="example/whisper")
        (snapshot / "weights.npz").unlink()
        (snapshot / "weights.safetensors").write_bytes(b"weights")
        local_asr.transcribe(str(self.audio), "example/whisper")
        self.assertEqual(self.whisper.transcribe.call_args.kwargs["path_or_hf_repo"], str(snapshot.resolve()))

    def test_incomplete_cache_falls_back_to_repo(self):
        _, snapshot = self.snapshot()
        for mode in ("broken-link", "empty-weights", "invalid-config"):
            with self.subTest(mode=mode):
                weights = snapshot / "weights.npz"
                weights.unlink()
                if mode == "broken-link":
                    weights.symlink_to(snapshot / "missing-blob")
                else:
                    weights.write_bytes(b"" if mode == "empty-weights" else b"weights")
                (snapshot / "config.json").write_text("invalid" if mode == "invalid-config" else '{"n_mels": 80}')
                self.assertEqual(local_asr._model_source(local_asr.DEFAULT_MODEL), local_asr.DEFAULT_MODEL)

    def test_decode_errors_propagate(self):
        self.whisper.transcribe.side_effect = RuntimeError("ffmpeg decode failed")
        with self.assertRaisesRegex(RuntimeError, "ffmpeg decode failed"):
            local_asr.transcribe(str(self.audio))

    def test_empty_transcription_is_preserved(self):
        self.whisper.transcribe.return_value = {"text": "", "segments": [], "language": "en"}
        self.assertEqual(local_asr.transcribe(str(self.audio)), {"text": "", "segments": [], "language": "en"})

    def test_missing_dependency_explained(self):
        with patch.object(local_asr.importlib, "import_module", side_effect=ModuleNotFoundError("mlx_whisper")):
            with self.assertRaisesRegex(RuntimeError, "requires mlx-whisper"):
                local_asr.transcribe(str(self.audio))

    def test_unload_synchronizes_then_releases_model_then_clears_cache(self):
        holder = types.SimpleNamespace(model=object(), model_path="snapshot")
        events = []
        def synchronize():
            self.assertIsNotNone(holder.model)
            events.append("sync")
        def clear_cache():
            self.assertIsNone(holder.model)
            self.assertIsNone(holder.model_path)
            events.append("clear")
        mx = types.SimpleNamespace(synchronize=synchronize, clear_cache=clear_cache)
        module = types.SimpleNamespace(ModelHolder=holder)
        with patch.dict(sys.modules, {"mlx_whisper.transcribe": module, "mlx.core": mx}):
            local_asr.unload()
        self.assertEqual(events, ["sync", "clear"])

    def test_unload_legacy_metal_cache_and_repeated_calls(self):
        holder = types.SimpleNamespace(model=object(), model_path="snapshot")
        clear = Mock()
        mx = types.SimpleNamespace(metal=types.SimpleNamespace(clear_cache=clear))
        with patch.dict(sys.modules, {
            "mlx_whisper.transcribe": types.SimpleNamespace(ModelHolder=holder), "mlx.core": mx,
        }):
            local_asr.unload()
            local_asr.unload()
        self.assertEqual(clear.call_count, 2)
        self.assertIsNone(holder.model)

    def test_unload_before_import_does_not_load_mlx(self):
        with patch.dict(sys.modules, {"mlx_whisper.transcribe": None, "mlx.core": None}):
            with patch.object(local_asr.importlib, "import_module") as load:
                local_asr.unload()
                load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
