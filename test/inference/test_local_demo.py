import importlib
import subprocess
import sys
from pathlib import Path

from inference.ShardConfig import ModelFamily, ShardConfig


class _FakeLocalEngine:
    def __init__(self, model_id, family, device="cpu"):
        self.model_id = model_id
        self.family = family
        self.device = device
        self.shard = type(
            "FakeShardContainer",
            (),
            {
                "shard_config": ShardConfig(
                    model_id=model_id,
                    family=family,
                    start_layer=0,
                    end_layer=24,
                    total_layers=24,
                    dtype="float16",
                )
            },
        )()

    def generate_stream(self, prompt, max_tokens=256, temperature=0.0):
        yield "Hi"
        yield "!"


class _FakeDistributedEngine:
    def __init__(
        self,
        model_id,
        family,
        shard_configs,
        device="cpu",
        local_shard_index=None,
        p2p_client=None,
    ):
        self.model_id = model_id
        self.family = family
        self.shard_configs = shard_configs
        self.device = device

    def generate_stream(self, prompt, max_tokens=256, temperature=0.0):
        yield "A"
        yield "B"


def test_local_demo_runs_both_modes_and_prints_explicit_load_summary(
    monkeypatch, capsys
):
    demo = importlib.import_module("scripts.local_demo")

    monkeypatch.setattr(demo, "LocalInferenceEngine", _FakeLocalEngine)
    monkeypatch.setattr(demo, "DistributedInferenceEngine", _FakeDistributedEngine)

    exit_code = demo.main(
        [
            "--mode",
            "both",
            "--model-id",
            "Qwen/Qwen2.5-0.5B-Instruct",
            "--family",
            "qwen",
            "--device",
            "cpu",
            "--max-tokens",
            "4",
            "--prompt",
            "Hello demo",
        ]
    )

    captured = capsys.readouterr()
    output = captured.out

    assert exit_code == 0
    assert "Demo A: single-node local" in output
    assert "Model: Qwen/Qwen2.5-0.5B-Instruct" in output
    assert "Family: qwen" in output
    assert "Device: cpu" in output
    assert "Total layers: 24" in output
    assert "Loaded layers: 24/24" in output
    assert "Demo B: 2-shard distributed simulation" in output
    assert "Shard 0: layers 0-12 (12 layers)" in output
    assert "Shard 1: layers 12-24 (12 layers)" in output
    assert "Prompt: Hello demo" in output
    assert "Final output: Hi!" in output
    assert "Final output: AB" in output


def test_local_demo_mentions_runtime_and_cluster_demos_for_follow_up(
    monkeypatch, capsys
):
    demo = importlib.import_module("scripts.local_demo")
    monkeypatch.setattr(demo, "LocalInferenceEngine", _FakeLocalEngine)
    monkeypatch.setattr(demo, "DistributedInferenceEngine", _FakeDistributedEngine)

    exit_code = demo.main(
        [
            "--mode",
            "both",
            "--model-id",
            "Qwen/Qwen2.5-0.5B-Instruct",
            "--family",
            "qwen",
            "--device",
            "cpu",
            "--prompt",
            "Hello",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "See also: scripts/cluster_planner_demo.py" in output
    assert "See also: scripts/runtime_lifecycle_demo.py" in output


def test_local_demo_cli_help_runs_directly_from_repo_root():
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [sys.executable, "scripts/local_demo.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Run a local ParaMind inference demo" in result.stdout
