from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    base_app_data_dir: Path
    app_data_dir: Path
    database_path: Path
    instance_id: str
    instance_name: str
    backend_port: int
    coordinator_port: int
    coordinator_base_url: str
    coordinator_ws_url: str
    model_id: str
    family: str
    device: str
    transport: str
    mock_inference_delay: float
    test_mode: bool


def _default_data_dir() -> Path:
    env_dir = os.environ.get("PARAMIND_APP_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".paramind-desktop"


def build_settings(test_mode: bool = False, overrides: dict | None = None) -> Settings:
    overrides = overrides or {}
    base_app_data_dir = Path(overrides.get("app_data_dir") or _default_data_dir())
    base_app_data_dir.mkdir(parents=True, exist_ok=True)

    backend_port = int(
        overrides.get("backend_port") or os.environ.get("PARAMIND_BACKEND_PORT", "5001")
    )
    instance_id = str(
        overrides.get("instance_id")
        or os.environ.get("PARAMIND_INSTANCE_ID")
        or f"peer-{uuid.uuid4().hex[:8]}"
    )
    instance_name = str(
        overrides.get("instance_name")
        or os.environ.get("PARAMIND_INSTANCE_NAME")
        or f"Local Node {backend_port}"
    )
    coordinator_port = int(
        overrides.get("coordinator_port")
        or os.environ.get("PARAMIND_COORDINATOR_PORT", "9010")
    )
    default_instance_dir = (
        base_app_data_dir
        if test_mode
        else (base_app_data_dir / "instances" / instance_id)
    )
    app_data_dir = Path(
        overrides.get("instance_data_dir")
        or os.environ.get("PARAMIND_INSTANCE_DATA_DIR")
        or default_instance_dir
    )
    app_data_dir.mkdir(parents=True, exist_ok=True)
    model_id = str(
        overrides.get("model_id")
        or os.environ.get("PARAMIND_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
    )
    family = str(overrides.get("family") or os.environ.get("PARAMIND_FAMILY", "qwen"))
    device = str(overrides.get("device") or os.environ.get("PARAMIND_DEVICE", "cpu"))
    transport = str(
        overrides.get("transport")
        or os.environ.get("PARAMIND_TRANSPORT")
        or ("mock-local" if test_mode else "localhost-coordinator")
    )
    mock_inference_delay = float(overrides.get("mock_inference_delay", 0.03))

    return Settings(
        base_app_data_dir=base_app_data_dir,
        app_data_dir=app_data_dir,
        database_path=app_data_dir / "paramind_desktop.sqlite3",
        instance_id=instance_id,
        instance_name=instance_name,
        backend_port=backend_port,
        coordinator_port=coordinator_port,
        coordinator_base_url=f"http://127.0.0.1:{coordinator_port}",
        coordinator_ws_url=f"ws://127.0.0.1:{coordinator_port}/ws/coordinator",
        model_id=model_id,
        family=family,
        device=device,
        transport=transport,
        mock_inference_delay=mock_inference_delay,
        test_mode=test_mode,
    )
