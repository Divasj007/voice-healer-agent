from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "skills" / "voice-healer" / "scripts" / "main.py"
spec = importlib.util.spec_from_file_location("voice_healer_main", MODULE_PATH)
main = importlib.util.module_from_spec(spec)
assert spec.loader is not None
import sys
sys.modules["voice_healer_main"] = main
spec.loader.exec_module(main)


class MainTests(unittest.TestCase):
    def test_safe_target_accepts_repo_python_file(self) -> None:
        target = main.safe_target("sample_bug.py")
        self.assertEqual(target.name, "sample_bug.py")
        self.assertEqual(target.suffix, ".py")

    def test_safe_target_rejects_path_escape(self) -> None:
        with self.assertRaises(ValueError):
            main.safe_target(os.path.join("..", "outside.py"))

    def test_safe_target_rejects_non_python(self) -> None:
        with self.assertRaises(ValueError):
            main.safe_target("README.md")

    def test_help_command_succeeds(self) -> None:
        self.assertEqual(main.handle_command("help"), 0)

    def test_quit_command_returns_sentinel(self) -> None:
        self.assertEqual(main.handle_command("quit"), 99)

    def test_run_command_routes_to_target(self) -> None:
        with patch.object(main, "run_process", return_value=0) as mocked:
            result = main.handle_command("run sample_bug.py")
        self.assertEqual(result, 0)
        command = mocked.call_args.args[0]
        self.assertEqual(Path(command[-1]).name, "sample_bug.py")


    def test_voice_command_strips_filler_and_punctuation(self) -> None:
        self.assertEqual(main.normalize_voice_command("Can you please run sample bug."), "run sample bug")

    def test_voice_target_normalizes_spoken_filename(self) -> None:
        self.assertEqual(main.normalize_voice_target("sample bug dot py"), "sample_bug.py")
        self.assertEqual(main.normalize_voice_target("sample underscore bug dot py"), "sample_bug.py")
        self.assertEqual(main.normalize_voice_target("sample button dot py"), "sample_bug.py")

    def test_voice_run_alias_routes_to_sample_bug(self) -> None:
        with patch.object(main, "run_process", return_value=0) as mocked:
            result = main.handle_command("I run sample bug")
        self.assertEqual(result, 0)
        command = mocked.call_args.args[0]
        self.assertEqual(Path(command[-1]).name, "sample_bug.py")

    def test_git_add_rejects_path_escape(self) -> None:
        with patch.object(main, "run_process") as mocked:
            result = main.handle_command("git add ../outside.py")
        self.assertEqual(result, 2)
        mocked.assert_not_called()

    def test_git_diff_rejects_output_option(self) -> None:
        with patch.object(main, "run_process") as mocked:
            result = main.handle_command("git diff --output=../outside.txt")
        self.assertEqual(result, 2)
        mocked.assert_not_called()

    def test_git_log_rejects_extra_options(self) -> None:
        with patch.object(main, "run_process") as mocked:
            result = main.handle_command("git log --format=%H")
        self.assertEqual(result, 2)
        mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()

    def test_verbose_help_run_utterance_uses_first_supported_command(self) -> None:
        with patch.object(main, "run_process", return_value=0) as mocked:
            result = main.handle_command("Help, run sample bug.")
        self.assertEqual(result, 0)
        mocked.assert_not_called()

    def test_verbose_run_utterance_routes_to_sample_bug(self) -> None:
        with patch.object(main, "run_process", return_value=0) as mocked:
            result = main.handle_command("Okay, go ahead and run sample bug.")
        self.assertEqual(result, 0)
        command = mocked.call_args.args[0]
        self.assertEqual(Path(command[-1]).name, "sample_bug.py")
