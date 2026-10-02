from __future__ import annotations

import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "voice-healer" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import local_endpoint  # noqa: E402


class LocalEndpointTests(unittest.TestCase):
    def test_accepts_localhost_url(self) -> None:
        self.assertEqual(
            local_endpoint.validate_local_http_url("http://localhost:11434/api/generate", label="url"),
            "http://localhost:11434/api/generate",
        )

    def test_accepts_ipv4_loopback_url(self) -> None:
        self.assertEqual(
            local_endpoint.validate_local_http_url("http://127.0.0.1:11434/api/generate", label="url"),
            "http://127.0.0.1:11434/api/generate",
        )

    def test_accepts_ipv6_loopback_url(self) -> None:
        self.assertEqual(
            local_endpoint.validate_local_http_url("http://[::1]:11434/api/generate", label="url"),
            "http://[::1]:11434/api/generate",
        )

    def test_rejects_remote_url(self) -> None:
        with self.assertRaises(ValueError):
            local_endpoint.validate_local_http_url("http://example.com/api/generate", label="url")

    def test_rejects_embedded_credentials(self) -> None:
        with self.assertRaises(ValueError):
            local_endpoint.validate_local_http_url("http://user:pass@127.0.0.1:11434/api/generate", label="url")

    def test_rejects_https_for_local_endpoint_policy(self) -> None:
        with self.assertRaises(ValueError):
            local_endpoint.validate_local_http_url("https://localhost:11434/api/generate", label="url")

    def test_rejects_public_bind_host(self) -> None:
        with self.assertRaises(ValueError):
            local_endpoint.validate_loopback_bind_host("0.0.0.0")

    def test_accepts_loopback_bind_host(self) -> None:
        self.assertEqual(local_endpoint.validate_loopback_bind_host("127.0.0.1"), "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
