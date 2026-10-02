#!/usr/bin/env python3
"""Static safety inspection for AI-generated Python repair candidates."""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path
from typing import Any

RULES_VERSION = "1.0"

HIGH_RULES = {
    "dynamic-code": "Dynamic code execution can execute model-controlled content.",
    "shell-execution": "Shell or subprocess execution can escape the intended repair scope.",
    "network-access": "Network access can exfiltrate source code or change remote state.",
    "destructive-delete": "File deletion or recursive removal can destroy project data.",
    "credential-access": "Credential or secret-environment access can expose sensitive values.",
    "native-code": "Native-code loading can bypass Python-level safety controls.",
    "pickle-execution": "Unsafe pickle deserialization can execute attacker-controlled payloads.",
    "path-escape-write": "Writing to an absolute or traversal path can modify files outside the intended scope.",
}

MEDIUM_RULES = {
    "filesystem-write": "The patch introduces filesystem mutation and should be reviewed carefully.",
    "permissions": "The patch changes filesystem permissions or ownership.",
    "environment-access": "The patch reads process environment values and may access sensitive configuration.",
}

NETWORK_MODULES = {
    "socket",
    "requests",
    "httpx",
    "urllib.request",
    "urllib3",
    "ftplib",
    "telnetlib",
    "paramiko",
    "websocket",
}
DYNAMIC_CALLS = {"eval", "exec", "compile", "__import__"}
SHELL_CALLS = {
    "os.system",
    "os.popen",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
}
DELETE_CALLS = {
    "os.remove",
    "os.unlink",
    "shutil.rmtree",
    "pathlib.Path.unlink",
    "Path.unlink",
}
NATIVE_MODULES = {"ctypes", "cffi"}
PICKLE_CALLS = {"pickle.load", "pickle.loads"}
PERMISSION_CALLS = {"os.chmod", "os.chown", "os.lchown", "os.fchmod", "os.fchown"}
WRITE_METHODS = {"write_text", "write_bytes", "unlink", "touch"}
SECRET_NAME_RE = re.compile(r"(?:token|secret|password|passwd|credential|api[_-]?key|private[_-]?key|access[_-]?key|auth)", re.I)
WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return None
    if isinstance(node, ast.Call) and _qualified_name(node.func) in {"Path", "pathlib.Path"}:
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value
    return None


def _source_line(source: str, line: int) -> str:
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()[:180]
    return ""


def _finding(rule: str, severity: str, node: ast.AST, source: str, detail: str) -> dict[str, Any]:
    line = getattr(node, "lineno", 0) or 0
    fingerprint = f"{rule}|{detail}"
    return {
        "rule": rule,
        "severity": severity,
        "message": HIGH_RULES.get(rule) or MEDIUM_RULES.get(rule) or detail,
        "detail": detail,
        "line": line,
        "column": (getattr(node, "col_offset", 0) or 0) + 1,
        "snippet": _source_line(source, line),
        "fingerprint": fingerprint,
    }


def _is_suspicious_path(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("~/") or WINDOWS_ABSOLUTE_RE.match(value):
        return True
    return any(part == ".." for part in normalized.split("/"))


def _write_mode(call: ast.Call) -> str | None:
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str):
        return call.args[1].value
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
    return None


def _env_access_detail(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return f"environment lookup: {node.args[0].value}"
        return "environment lookup"
    return "os.environ access"


def scan_source(source: str) -> list[dict[str, Any]]:
    tree = ast.parse(source)
    findings: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                root = module.split(".", 1)[0]
                if module in NETWORK_MODULES or root in NETWORK_MODULES:
                    findings.append(_finding("network-access", "high", node, source, f"import {module}"))
                if root in NATIVE_MODULES:
                    findings.append(_finding("native-code", "high", node, source, f"import {module}"))
                if root == "subprocess":
                    findings.append(_finding("shell-execution", "high", node, source, "import subprocess"))
                if root == "pickle":
                    findings.append(_finding("pickle-execution", "high", node, source, "import pickle"))

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            imported_names = {alias.name for alias in node.names}
            if module in NETWORK_MODULES or root in NETWORK_MODULES or root == "urllib":
                findings.append(_finding("network-access", "high", node, source, f"from {module} import ..."))
            if root in NATIVE_MODULES:
                findings.append(_finding("native-code", "high", node, source, f"from {module} import ..."))
            if root == "subprocess":
                findings.append(_finding("shell-execution", "high", node, source, f"from {module} import ..."))
            if root == "os" and imported_names.intersection({"system", "popen"}):
                findings.append(_finding("shell-execution", "high", node, source, f"from {module} import system/popen"))
            if root == "os" and imported_names.intersection({"remove", "unlink"}):
                findings.append(_finding("destructive-delete", "high", node, source, f"from {module} import remove/unlink"))
            if root == "shutil" and "rmtree" in imported_names:
                findings.append(_finding("destructive-delete", "high", node, source, "from shutil import rmtree"))
            if root == "pickle":
                findings.append(_finding("pickle-execution", "high", node, source, f"from {module} import ..."))

        elif isinstance(node, ast.Call):
            name = _qualified_name(node.func)
            if name in DYNAMIC_CALLS:
                findings.append(_finding("dynamic-code", "high", node, source, f"call {name}()"))
            if name in SHELL_CALLS or name.startswith("subprocess."):
                findings.append(_finding("shell-execution", "high", node, source, f"call {name}()"))
            if name in DELETE_CALLS or name == "unlink" or name.endswith(".unlink") or name.endswith(".rmtree"):
                findings.append(_finding("destructive-delete", "high", node, source, f"call {name}()"))
            if name in PICKLE_CALLS:
                findings.append(_finding("pickle-execution", "high", node, source, f"call {name}()"))
            if name.startswith("socket.") or name.startswith("requests.") or name.startswith("httpx.") or name.startswith("urllib.request."):
                findings.append(_finding("network-access", "high", node, source, f"call {name}()"))
            if any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True for keyword in node.keywords):
                findings.append(_finding("shell-execution", "high", node, source, "call uses shell=True"))
            if name in PERMISSION_CALLS:
                findings.append(_finding("permissions", "medium", node, source, f"call {name}()"))
            if name == "os.getenv" or name.endswith(".getenv"):
                detail = _env_access_detail(node)
                key = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) else ""
                rule = "credential-access" if SECRET_NAME_RE.search(key) else "environment-access"
                severity = "high" if rule == "credential-access" else "medium"
                findings.append(_finding(rule, severity, node, source, detail))
            if name == "getpass.getpass":
                findings.append(_finding("credential-access", "high", node, source, "getpass.getpass()"))
            if name == "keyring.get_password":
                findings.append(_finding("credential-access", "high", node, source, "keyring.get_password()"))
            if name == "os.environ.get" or name.startswith("os.environ"):
                findings.append(_finding("credential-access", "high", node, source, "os.environ access"))

            if name == "open":
                mode = _write_mode(node)
                path_value = _literal_string(node.args[0]) if node.args else None
                if mode and any(flag in mode for flag in ("w", "a", "x", "+")):
                    rule = "path-escape-write" if _is_suspicious_path(path_value) else "filesystem-write"
                    severity = "high" if rule == "path-escape-write" else "medium"
                    detail = f"open(..., mode={mode!r})" + (f" path={path_value!r}" if path_value else "")
                    findings.append(_finding(rule, severity, node, source, detail))

            if name in {"write_text", "write_bytes", "touch"} or name.endswith(".write_text") or name.endswith(".write_bytes") or name.endswith(".touch"):
                receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
                path_value = _literal_string(receiver)
                rule = "path-escape-write" if _is_suspicious_path(path_value) else "filesystem-write"
                severity = "high" if rule == "path-escape-write" else "medium"
                findings.append(_finding(rule, severity, node, source, f"call {name}()" + (f" path={path_value!r}" if path_value else "")))

            if name in {"os.replace", "os.rename", "shutil.move"}:
                destination = _literal_string(node.args[-1]) if node.args else None
                rule = "path-escape-write" if _is_suspicious_path(destination) else "filesystem-write"
                severity = "high" if rule == "path-escape-write" else "medium"
                findings.append(_finding(rule, severity, node, source, f"call {name}()" + (f" destination={destination!r}" if destination else "")))

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _qualified_name(node.value) == "os.environ":
            findings.append(_finding("credential-access", "high", node, source, "os.environ[...] access"))
        elif isinstance(node, ast.Attribute) and _qualified_name(node) == "os.environ":
            findings.append(_finding("credential-access", "high", node, source, "os.environ access"))

    return findings


def inspect_candidate(source: str, candidate: str, repo_root: Path | None = None) -> dict[str, Any]:
    del repo_root
    baseline = scan_source(source)
    proposed = scan_source(candidate)
    baseline_counts = Counter(item["fingerprint"] for item in baseline)
    introduced: list[dict[str, Any]] = []
    used: Counter[str] = Counter()
    for item in proposed:
        fingerprint = item["fingerprint"]
        if used[fingerprint] < baseline_counts[fingerprint]:
            used[fingerprint] += 1
            continue
        introduced.append({key: value for key, value in item.items() if key != "fingerprint"})
        used[fingerprint] += 1

    introduced.sort(key=lambda item: (0 if item["severity"] == "high" else 1, item["line"], item["rule"]))
    high_count = sum(item["severity"] == "high" for item in introduced)
    medium_count = sum(item["severity"] == "medium" for item in introduced)
    blocked = high_count > 0
    if blocked:
        overall = "blocked"
        summary = f"Safety Guard blocked this patch: {high_count} high-risk change(s) detected."
    elif medium_count:
        overall = "warning"
        summary = f"Safety Guard passed with warnings: {medium_count} review item(s) detected."
    else:
        overall = "safe"
        summary = "Safety Guard found no newly introduced high-risk operations."

    return {
        "rules_version": RULES_VERSION,
        "overall": overall,
        "blocked": blocked,
        "summary": summary,
        "baseline_findings": len(baseline),
        "candidate_findings": len(proposed),
        "finding_count": len(introduced),
        "high_count": high_count,
        "medium_count": medium_count,
        "findings": introduced,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Inspect an AI-generated Python candidate for risky operations.")
    parser.add_argument("source", type=Path, help="Current Python source file.")
    parser.add_argument("candidate", type=Path, help="Candidate Python source file.")
    args = parser.parse_args()
    source = args.source.read_text(encoding="utf-8")
    candidate = args.candidate.read_text(encoding="utf-8")
    report = inspect_candidate(source, candidate, Path.cwd())
    import json
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 2 if report["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
