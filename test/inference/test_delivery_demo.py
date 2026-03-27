import importlib
import json


class _FakeLocalEngine:
    def __init__(self, model_id, family, device="cpu"):
        self.model_id = model_id
        self.family = family
        self.device = device
        self.shard = type(
            "FakeShardContainer",
            (),
            {
                "shard_config": type(
                    "FakeShardConfig",
                    (),
                    {
                        "num_layers": 24,
                        "start_layer": 0,
                        "end_layer": 24,
                    },
                )()
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


def test_overview_default_output_mentions_four_demo_tracks(capsys):
    demo = importlib.import_module("scripts.delivery_demo")

    exit_code = demo.main([])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "delivery demo" in output.lower()
    assert "local" in output.lower()
    assert "planner" in output.lower()
    assert "lifecycle" in output.lower()
    assert "status" in output.lower()


def test_status_output_includes_phase_commit_and_shard_runtime_fields(capsys):
    demo = importlib.import_module("scripts.delivery_demo")

    exit_code = demo.main(["status"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "phase" in output
    assert "reconfiguration_committed" in output
    assert "pending_unload" in output
    assert "inflight_requests" in output


def test_status_json_returns_machine_readable_snapshot(capsys):
    demo = importlib.import_module("scripts.delivery_demo")

    exit_code = demo.main(["status", "--json"])
    output = capsys.readouterr().out

    payload = json.loads(output)

    assert exit_code == 0
    assert "final_snapshot" in payload
    assert "coordinator" in payload["final_snapshot"]
    assert "runtimes" in payload["final_snapshot"]


def test_planner_scripted_runs_fixed_command_sequence(capsys):
    demo = importlib.import_module("scripts.delivery_demo")

    exit_code = demo.main(["planner", "--scripted"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "CURRENT CLUSTER" in output
    assert "EVENT LOG" in output
    assert "join node-d" in output


def test_all_runs_delivery_steps_in_stable_order(monkeypatch, capsys):
    delivery_demo = importlib.import_module("scripts.delivery_demo")
    local_demo = importlib.import_module("scripts.local_demo")

    monkeypatch.setattr(local_demo, "LocalInferenceEngine", _FakeLocalEngine)
    monkeypatch.setattr(
        local_demo, "DistributedInferenceEngine", _FakeDistributedEngine
    )

    exit_code = delivery_demo.main(["all"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "=== DELIVERY STEP 1: LOCAL ===" in output
    assert "=== DELIVERY STEP 2: PLANNER ===" in output
    assert "=== DELIVERY STEP 3: LIFECYCLE ===" in output
    assert "=== DELIVERY STEP 4: STATUS ===" in output
