"""Config lookup: process env → GCP Secret Manager / JSON file → missing.

Production (Docker on GCP): set ``VPM_CONFIG_SOURCE=gcp``. The secret payload is a
flat JSON object (same keys as the old ``.env`` / ``vpm_config.example.json``).

Default secret::

    projects/605301150765/secrets/DEV-VOYAGEPM-AGENTIC-AGENT

Override with ``VPM_GCP_SECRET`` (full resource or secret id) and optional
``VPM_GCP_SECRET_VERSION`` (default ``latest``).

Local: omit ``VPM_CONFIG_SOURCE`` / use ``file`` — loads ``VPM_CONFIG_JSON`` or
``./vpm_config.json``. Process env always wins per key (Compose ``env_file`` ok).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

# VoyagePM agentic agent — DEV secret in project 605301150765
_DEFAULT_GCP_SECRET = "projects/605301150765/secrets/DEV-VOYAGEPM-AGENTIC-AGENT"

_UNSET: Any = object()
_data: dict[str, str] | None = None
_source_label: str = ""
_warned: set[str] = set()


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _parse_payload(text: str) -> dict[str, str]:
    """JSON object preferred; otherwise KEY=VALUE lines (dotenv-style)."""
    text = (text or "").strip()
    if not text:
        return {}
    if text[0] in "{[":
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError("config JSON root must be an object")
        out: dict[str, str] = {}
        for k, v in raw.items():
            if isinstance(v, dict):
                continue
            out[str(k)] = _as_str(v)
        return out
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k = k.strip()
        if k:
            out[k] = v.strip().strip('"').strip("'")
    return out


def _candidate_paths() -> list[Path]:
    out: list[Path] = []
    for key in ("VPM_CONFIG_JSON", "VPM_CONFIG_PATH"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            out.append(Path(raw).expanduser())
    out.extend([ROOT / "vpm_config.json", Path("/etc/vpm/config.json")])
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def _load_from_file() -> tuple[dict[str, str], str] | None:
    for path in _candidate_paths():
        if not path.is_file():
            continue
        try:
            data = _parse_payload(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[vpm_cfg] failed to read {path}: {e}", file=sys.stderr, flush=True)
            continue
        return data, str(path)
    return None


def _secret_resource_name() -> str:
    raw = (os.environ.get("VPM_GCP_SECRET") or "").strip() or _DEFAULT_GCP_SECRET
    if raw.startswith("projects/") and "/secrets/" in raw:
        base = raw
    else:
        project = (
            (os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
            or (os.environ.get("GCP_PROJECT") or "").strip()
            or "605301150765"
        )
        base = f"projects/{project}/secrets/{raw}"
    if "/versions/" in base:
        return base
    version = (os.environ.get("VPM_GCP_SECRET_VERSION") or "latest").strip() or "latest"
    return f"{base}/versions/{version}"


def _load_from_gcp() -> tuple[dict[str, str], str]:
    try:
        from google.cloud import secretmanager
    except ImportError as e:
        raise RuntimeError(
            "google-cloud-secret-manager required for VPM_CONFIG_SOURCE=gcp — "
            "pip install google-cloud-secret-manager"
        ) from e
    name = _secret_resource_name()
    client = secretmanager.SecretManagerServiceClient()
    resp = client.access_secret_version(request={"name": name})
    payload = resp.payload.data.decode("utf-8")
    data = _parse_payload(payload)
    return data, name


def _wanted_source() -> str:
    """auto | file | gcp (aliases: secret, secretmanager)."""
    raw = (os.environ.get("VPM_CONFIG_SOURCE") or "auto").strip().lower()
    if raw in ("secret", "secretmanager", "gsm"):
        return "gcp"
    if raw in ("json", "local"):
        return "file"
    return raw or "auto"


def _load_data() -> dict[str, str]:
    global _data, _source_label
    if _data is not None:
        return _data

    source = _wanted_source()
    data: dict[str, str] = {}
    label = "(none)"

    if source == "gcp":
        data, label = _load_from_gcp()
    elif source == "file":
        found = _load_from_file()
        if found:
            data, label = found
    else:
        # auto: local file if present, else GCP when explicitly pointed at a secret
        found = _load_from_file()
        if found:
            data, label = found
        elif (os.environ.get("VPM_GCP_SECRET") or "").strip():
            data, label = _load_from_gcp()

    _data = data
    _source_label = label
    if data:
        print(f"[vpm_cfg] loaded {label} ({len(data)} keys)", flush=True)
    elif source == "gcp":
        print(f"[vpm_cfg] GCP secret {label} had no keys", file=sys.stderr, flush=True)
    return _data


def reload() -> None:
    """Drop cached config (tests / after secret rotate)."""
    global _data, _source_label
    _data = None
    _source_label = ""
    _warned.clear()


def config_source_label() -> str:
    _load_data()
    return _source_label


def config_path() -> Path | None:
    """Path when loaded from a file; None for GCP / empty."""
    label = config_source_label()
    if label.startswith("/") or label.endswith(".json"):
        p = Path(label)
        return p if p.is_file() else None
    return None


def has(key: str) -> bool:
    if key in os.environ:
        return True
    return key in _load_data()


def get(key: str, default: Any = _UNSET) -> str:
    """Process env if set, else secret/JSON, else default / empty + 'env variable not set'."""
    if key in os.environ:
        return os.environ[key]
    data = _load_data()
    if key in data:
        return data[key]
    if default is _UNSET:
        if key not in _warned:
            _warned.add(key)
            print(f"env variable not set: {key}", file=sys.stderr, flush=True)
        return ""
    return _as_str(default)


def require(key: str) -> str:
    """Like get without default; raises if missing from env and config store."""
    if has(key):
        return get(key, "")
    msg = f"env variable not set: {key}"
    print(msg, file=sys.stderr, flush=True)
    raise RuntimeError(msg)
