#!/usr/bin/env python3
"""Offline microphone capture, Whisper transcription, and local TTS feedback."""

from __future__ import annotations

import argparse
import io
import math
import sys
import tempfile
import wave
from pathlib import Path
from typing import Optional, Sequence

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None  # type: ignore[assignment]

try:
    import speech_recognition as sr
except ImportError:
    sr = None  # type: ignore[assignment]

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None  # type: ignore[assignment,misc]


PREFIX = "[Voice Agent]"
INITIAL_PROMPT = (
    "Local developer voice commands. Supported commands include help, run, heal, fix, doctor, "
    "git status, git diff, git log, git add, git commit, quit. "
    "Python targets may be spoken as sample bug, sample bug dot py, or sample underscore bug dot py."
)


class VoiceIO:
    """Offline voice I/O with graceful text fallback when audio is unavailable."""

    def __init__(self, timeout: int = 6, phrase_time_limit: int = 10) -> None:
        self.timeout = timeout
        self.phrase_time_limit = phrase_time_limit
        self._recognizer = sr.Recognizer() if sr is not None else None
        if self._recognizer is not None:
            self._recognizer.pause_threshold = 0.45
            self._recognizer.non_speaking_duration = 0.30
            self._recognizer.phrase_threshold = 0.20
        self._model = None
        self._tts = None
        self._tts_enabled = False
        self._ambient_calibrated = False

    def _load_model(self) -> bool:
        if self._model is not None:
            return True
        if WhisperModel is None:
            print(f"{PREFIX} faster-whisper is not installed; using text input.", file=sys.stderr)
            return False
        try:
            print(f"{PREFIX} Loading offline Whisper tiny model...", flush=True)
            self._model = WhisperModel("tiny", device="cpu", compute_type="int8")
            return True
        except Exception as exc:
            print(f"{PREFIX} Whisper unavailable: {exc}. Falling back to CLI input.", file=sys.stderr)
            self._model = None
            return False

    def _load_tts(self) -> None:
        if self._tts is not None or not self._tts_enabled:
            return
        if pyttsx3 is None:
            self._tts_enabled = False
            return
        try:
            self._tts = pyttsx3.init()
            self._tts.setProperty("rate", 175)
            self._tts.setProperty("volume", 0.9)
        except Exception as exc:
            print(f"{PREFIX} TTS unavailable: {exc}", file=sys.stderr)
            self._tts = None
            self._tts_enabled = False

    def speak(self, message: str) -> None:
        """Speak locally when pyttsx3 is available; never fail the command path."""
        print(f"{PREFIX} {message}")
        if not self._tts_enabled:
            return
        self._load_tts()
        if self._tts is None:
            return
        try:
            self._tts.say(message)
            self._tts.runAndWait()
        except Exception as exc:
            print(f"{PREFIX} TTS error: {exc}", file=sys.stderr)
            self._tts = None
            self._tts_enabled = False

    def _record_wav(self) -> bytes:
        if sr is None or self._recognizer is None:
            raise RuntimeError("SpeechRecognition is not installed")

        try:
            with sr.Microphone(sample_rate=16000) as source:
                if not self._ambient_calibrated:
                    print(f"{PREFIX} Calibrating microphone...", flush=True)
                    self._recognizer.adjust_for_ambient_noise(source, duration=0.4)
                    self._ambient_calibrated = True
                print(f"{PREFIX} Listening... speak your command.", flush=True)
                audio = self._recognizer.listen(
                    source,
                    timeout=self.timeout,
                    phrase_time_limit=self.phrase_time_limit,
                )
                return audio.get_wav_data()
        except AttributeError as exc:
            raise RuntimeError("PyAudio is unavailable. Install PyAudio or use --cli.") from exc
        except Exception as exc:
            raise RuntimeError(f"Microphone/audio driver error: {exc}") from exc

    @staticmethod
    def _audio_stats(wav_bytes: bytes) -> tuple[float, float]:
        """Return RMS amplitude and duration for a PCM WAV byte string."""
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
                frames = reader.readframes(reader.getnframes())
                sample_width = reader.getsampwidth()
                sample_rate = reader.getframerate()
                channels = reader.getnchannels()
        except (wave.Error, EOFError, ValueError, OSError) as exc:
            raise RuntimeError(f"Invalid recorded audio: {exc}") from exc

        if not frames or sample_width not in {1, 2, 3, 4} or sample_rate <= 0 or channels <= 0:
            return 0.0, 0.0

        if sample_width == 1:
            samples = [byte - 128 for byte in frames]
            scale = 128.0
        elif sample_width == 2:
            samples = [int.from_bytes(frames[i : i + 2], "little", signed=True) for i in range(0, len(frames), 2)]
            scale = 32768.0
        elif sample_width == 3:
            samples = []
            for i in range(0, len(frames), 3):
                chunk = frames[i : i + 3]
                if len(chunk) < 3:
                    break
                value = int.from_bytes(chunk + (b"\xff" if chunk[2] & 0x80 else b"\x00"), "little", signed=True)
                samples.append(value)
            scale = 8388608.0
        else:
            samples = [int.from_bytes(frames[i : i + 4], "little", signed=True) for i in range(0, len(frames), 4)]
            scale = 2147483648.0

        if not samples:
            return 0.0, 0.0
        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / scale
        duration = len(samples) / (sample_rate * channels)
        return rms, duration

    @staticmethod
    def _looks_like_whisper_hallucination(text: str, segments: Sequence[object]) -> bool:
        normalized = " ".join(text.lower().split())
        if not normalized:
            return True

        if normalized.count(".py") >= 3:
            return True

        words = normalized.replace(".", " ").split()
        if len(words) >= 10 and len(set(words)) <= max(2, len(words) // 5):
            return True

        probabilities = [
            float(getattr(segment, "no_speech_prob", 0.0))
            for segment in segments
            if getattr(segment, "no_speech_prob", None) is not None
        ]
        logprobs = [
            float(getattr(segment, "avg_logprob", 0.0))
            for segment in segments
            if getattr(segment, "avg_logprob", None) is not None
        ]
        if probabilities and all(value >= 0.72 for value in probabilities):
            return True
        if logprobs and max(logprobs) < -1.2:
            return True
        return False

    def _transcribe_wav(self, wav_bytes: bytes) -> str:
        if not self._load_model():
            raise RuntimeError("Offline Whisper is unavailable")

        rms, duration = self._audio_stats(wav_bytes)
        if duration < 0.10 or rms < 0.006:
            raise RuntimeError("No speech was detected")

        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                handle.write(wav_bytes)
                temp_path = Path(handle.name)

            def transcribe_once(vad_filter: bool):
                kwargs = {
                    "language": "en",
                    "beam_size": 5,
                    "temperature": 0.0,
                    "vad_filter": vad_filter,
                    "condition_on_previous_text": False,
                    "initial_prompt": INITIAL_PROMPT,
                    "no_speech_threshold": 0.60,
                }
                if vad_filter:
                    kwargs["vad_parameters"] = {
                        "min_speech_duration_ms": 120,
                        "min_silence_duration_ms": 450,
                        "speech_pad_ms": 250,
                    }
                return self._model.transcribe(str(temp_path), **kwargs)

            segments, _ = transcribe_once(vad_filter=True)
            first_segments = list(segments)
            text = " ".join(segment.text.strip() for segment in first_segments).strip()

            if not text or self._looks_like_whisper_hallucination(text, first_segments):
                segments, _ = transcribe_once(vad_filter=False)
                second_segments = list(segments)
                text = " ".join(segment.text.strip() for segment in second_segments).strip()
                if not text or self._looks_like_whisper_hallucination(text, second_segments):
                    raise RuntimeError("No speech was detected")

            return text
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def listen(self, allow_cli_fallback: bool = True) -> str:
        """Listen once; retry one silence miss, then fall back to text input."""
        try:
            wav_bytes = self._record_wav()
            command = self._transcribe_wav(wav_bytes)
            print(f"{PREFIX} Heard: {command}")
            return command
        except (KeyboardInterrupt, EOFError):
            print(f"{PREFIX} Input cancelled.", file=sys.stderr)
            return ""
        except RuntimeError as exc:
            if str(exc) == "No speech was detected":
                print(f"{PREFIX} No clear speech detected. Please type the command.", file=sys.stderr)
            else:
                print(f"{PREFIX} Voice input failed: {exc}", file=sys.stderr)
            if not allow_cli_fallback:
                return ""
            return self.text_input()
        except Exception as exc:
            print(f"{PREFIX} Voice input failed: {exc}", file=sys.stderr)
            if not allow_cli_fallback:
                return ""
            return self.text_input()

    @staticmethod
    def text_input() -> str:
        try:
            return input(f"{PREFIX} Type command: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{PREFIX} Input cancelled.", file=sys.stderr)
            return ""


def build_voice_io(enable_tts: bool = True) -> VoiceIO:
    voice = VoiceIO()
    voice._tts_enabled = enable_tts
    return voice


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline microphone command listener.")
    parser.add_argument("--text", help="Use the supplied text command instead of the microphone.")
    parser.add_argument("--cli", action="store_true", help="Force manual text input.")
    parser.add_argument("--no-tts", action="store_true", help="Disable local text-to-speech.")
    args = parser.parse_args()

    voice = build_voice_io(enable_tts=not args.no_tts)
    if args.text is not None:
        print(args.text.strip())
        voice.speak(args.text.strip())
        return 0

    command = voice.text_input() if args.cli else voice.listen(allow_cli_fallback=True)
    if command:
        print(command)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
