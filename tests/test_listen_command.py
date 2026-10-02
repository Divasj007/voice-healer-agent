from __future__ import annotations

import importlib.util
import io
import sys
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

MODULE_PATH = ROOT / "skills" / "voice-healer" / "scripts" / "listen_command.py"
spec = importlib.util.spec_from_file_location("voice_healer_listen", MODULE_PATH)
listen = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["voice_healer_listen"] = listen
spec.loader.exec_module(listen)

MAIN_PATH = ROOT / "skills" / "voice-healer" / "scripts" / "main.py"
spec_main = importlib.util.spec_from_file_location("voice_healer_main_for_listen_tests", MAIN_PATH)
main = importlib.util.module_from_spec(spec_main)
assert spec_main.loader is not None
sys.modules["voice_healer_main_for_listen_tests"] = main
spec_main.loader.exec_module(main)


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def transcribe(self, *args, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        return iter(
            SimpleNamespace(text=text, no_speech_prob=0.01, avg_logprob=-0.1)
            for text in response
        ), SimpleNamespace()


def make_wav(amplitude: int = 1000, frames: int = 16000, rate: int = 16000) -> bytes:
    raw = b"".join(int(amplitude).to_bytes(2, "little", signed=True) for _ in range(frames))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(raw)
    return buffer.getvalue()


class ListenCommandTests(unittest.TestCase):
    def test_transcribe_uses_command_prompt_and_short_speech_vad(self) -> None:
        model = FakeModel([[" help "]])
        voice = listen.VoiceIO()
        voice._model = model
        with patch.object(voice, "_load_model", return_value=True):
            voice._transcribe_wav(make_wav())
        kwargs = model.calls[0]
        self.assertEqual(kwargs["initial_prompt"], listen.INITIAL_PROMPT)
        self.assertEqual(kwargs["vad_filter"], True)
        self.assertEqual(kwargs["vad_parameters"]["min_speech_duration_ms"], 120)
        self.assertEqual(kwargs["vad_parameters"]["speech_pad_ms"], 250)
        self.assertEqual(kwargs["temperature"], 0.0)
        self.assertNotIn("hotwords", kwargs)

    def test_transcribe_retries_without_vad_when_first_pass_is_empty(self) -> None:
        model = FakeModel([[], ["run sample bug"]])
        voice = listen.VoiceIO()
        voice._model = model
        with patch.object(voice, "_load_model", return_value=True):
            text = voice._transcribe_wav(make_wav())
        self.assertEqual(text, "run sample bug")
        self.assertEqual(model.calls[0]["vad_filter"], True)
        self.assertEqual(model.calls[1]["vad_filter"], False)

    def test_unclear_short_utterance_falls_back_without_re_record_loop(self) -> None:
        voice = listen.VoiceIO()
        with patch.object(voice, "_record_wav", return_value=b"audio"), patch.object(
            voice, "_transcribe_wav", side_effect=RuntimeError("No speech was detected")
        ), patch.object(voice, "text_input", return_value="help") as typed:
            self.assertEqual(voice.listen(allow_cli_fallback=True), "help")
            typed.assert_called_once()

    def test_transcribe_raises_when_both_passes_are_empty(self) -> None:
        model = FakeModel([[], []])
        voice = listen.VoiceIO()
        voice._model = model
        with patch.object(voice, "_load_model", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "No speech was detected"):
                voice._transcribe_wav(make_wav())


class ListenRobustnessTests(unittest.TestCase):
    def test_silence_is_rejected_before_whisper(self) -> None:
        voice = listen.VoiceIO()
        with patch.object(voice, "_load_model", return_value=True), patch.object(voice, "_model") as model:
            with self.assertRaisesRegex(RuntimeError, "No speech was detected"):
                voice._transcribe_wav(make_wav(0))
            model.transcribe.assert_not_called()

    def test_hallucinated_repeated_py_text_is_rejected(self) -> None:
        text = "dot py.py.py.py.py.py.py.py"
        segments = [SimpleNamespace(text=text, no_speech_prob=0.98, avg_logprob=-0.1)]
        self.assertTrue(voice_hallucination_check(text, segments))

    def test_low_confidence_transcription_is_rejected(self) -> None:
        text = "help"
        segments = [SimpleNamespace(text=text, no_speech_prob=0.95, avg_logprob=-0.2)]
        self.assertTrue(voice_hallucination_check(text, segments))

    def test_text_target_alias_normalizes_sample_button(self) -> None:
        self.assertEqual(main.normalize_voice_target("sample button"), "sample_bug.py")

    def test_verbose_help_utterance_extracts_help(self) -> None:
        self.assertEqual(main._extract_first_command("Help, run sample bug."), "Help")

    def test_verbose_run_utterance_extracts_run_command(self) -> None:
        self.assertEqual(main._extract_first_command("Okay, go ahead and run sample bug."), "run sample bug")


def voice_hallucination_check(text: str, segments: list[object]) -> bool:
    return listen.VoiceIO._looks_like_whisper_hallucination(text, segments)


if __name__ == "__main__":
    unittest.main()
