import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DESKTOP_PYTHON_ROOT = REPO_ROOT / "paramind" / "apps" / "desktop" / "python"


def _run_entrypoint(script: Path, tmp_path: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(DESKTOP_PYTHON_ROOT)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, runpy; "
                f"os.chdir({tmp_path.as_posix()!r}); "
                f"runpy.run_path({script.as_posix()!r}, run_name='__test__')"
            ),
        ],
        env=env,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )


def test_backend_entrypoint_imports_without_repo_root(tmp_path):
    result = _run_entrypoint(DESKTOP_PYTHON_ROOT / "backend.py", tmp_path)
    assert result.returncode == 0, result.stderr


def test_coordinator_entrypoint_imports_without_repo_root(tmp_path):
    result = _run_entrypoint(DESKTOP_PYTHON_ROOT / "app" / "coordinator.py", tmp_path)
    assert result.returncode == 0, result.stderr


def test_desktop_python_entrypoints_do_not_depend_on_repo_package_paths():
    backend_source = (DESKTOP_PYTHON_ROOT / "backend.py").read_text()
    coordinator_source = (DESKTOP_PYTHON_ROOT / "app" / "coordinator.py").read_text()

    assert "paramind.apps.desktop.python" not in backend_source
    assert "paramind.apps.desktop.python" not in coordinator_source
