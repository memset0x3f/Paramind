import importlib


def test_render_key_value_block_has_stable_header_and_alignment():
    rendering = importlib.import_module("scripts.demo_rendering")

    rendered = rendering.render_key_value_block(
        "CURRENT CLUSTER",
        {
            "model": "Qwen/Qwen2.5-0.5B-Instruct",
            "coordinator": "node-a",
        },
    )

    assert "=== CURRENT CLUSTER ===" in rendered
    assert "model       : Qwen/Qwen2.5-0.5B-Instruct" in rendered
    assert "coordinator : node-a" in rendered


def test_render_table_prints_headers_and_rows_in_plain_text():
    rendering = importlib.import_module("scripts.demo_rendering")

    rendered = rendering.render_table(
        "SHARD STATES",
        columns=["node", "shard", "state"],
        rows=[("node-a", "0-12", "serving")],
    )

    assert "[SHARD STATES]" in rendered
    assert "node" in rendered
    assert "0-12" in rendered
    assert "serving" in rendered


def test_runtime_lifecycle_demo_prints_prepare_and_commit_flow(capsys):
    demo = importlib.import_module("scripts.runtime_lifecycle_demo")

    exit_code = demo.main([])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "=== PARAMETER GUIDE ===" in output
    assert "=== CURRENT CLUSTER ===" in output
    assert "[EVENT LOG]" in output
    assert "cluster_reconfigure_prepare" in output
    assert "cluster_reconfigure_commit" in output
    assert "[SHARD STATES]" in output
    assert "serving" in output
    assert "pending_unload" in output


def test_runtime_lifecycle_demo_shows_before_prepare_after_prepare_and_after_commit(
    capsys,
):
    demo = importlib.import_module("scripts.runtime_lifecycle_demo")

    demo.main([])
    output = capsys.readouterr().out

    assert "=== BEFORE RECONFIGURATION ===" in output
    assert "=== AFTER PREPARE ===" in output
    assert "=== AFTER COMMIT ===" in output
    assert "node-a" in output
    assert "node-c" in output
    assert "ready_nodes" in output
    assert "reconfiguration_committed" in output
