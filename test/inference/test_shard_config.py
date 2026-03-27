import pytest
from inference.ShardConfig import ShardConfig, ModelFamily


def test_shard_config_creation():
    cfg = ShardConfig(
        model_id="Qwen/Qwen2.5-1.5B-Instruct",
        family=ModelFamily.QWEN,
        start_layer=0,
        end_layer=14,
        total_layers=28,
    )
    assert cfg.start_layer == 0
    assert cfg.end_layer == 14
    assert cfg.num_layers == 14
    assert cfg.is_first_shard is True
    assert cfg.is_last_shard is False
    assert cfg.dtype is None


def test_shard_config_last_shard():
    cfg = ShardConfig(
        model_id="Qwen/Qwen2.5-1.5B-Instruct",
        family=ModelFamily.QWEN,
        start_layer=14,
        end_layer=28,
        total_layers=28,
        dtype="float16",
    )
    assert cfg.is_first_shard is False
    assert cfg.is_last_shard is True


def test_shard_config_validation():
    with pytest.raises(ValueError):
        ShardConfig(
            model_id="Qwen/Qwen2.5-1.5B-Instruct",
            family=ModelFamily.QWEN,
            start_layer=14,
            end_layer=10,  # end < start
            total_layers=28,
        )
    with pytest.raises(ValueError):
        ShardConfig(
            model_id="Qwen/Qwen2.5-1.5B-Instruct",
            family=ModelFamily.QWEN,
            start_layer=0,
            end_layer=14,
            total_layers=28,
            dtype="not-a-real-dtype",
        )


def test_split_plan_even():
    """Split a 28-layer model across 2 nodes."""
    plans = ShardConfig.plan_split(
        model_id="Qwen/Qwen2.5-1.5B-Instruct",
        family=ModelFamily.QWEN,
        total_layers=28,
        num_nodes=2,
        dtype="float16",
    )
    assert len(plans) == 2
    assert plans[0].start_layer == 0
    assert plans[0].end_layer == 14
    assert plans[1].start_layer == 14
    assert plans[1].end_layer == 28


def test_split_plan_uneven():
    """Split a 24-layer model across 5 nodes."""
    plans = ShardConfig.plan_split(
        model_id="test",
        family=ModelFamily.QWEN,
        total_layers=24,
        num_nodes=5,
        dtype="float16",
    )
    assert len(plans) == 5
    assert plans[0].start_layer == 0
    assert plans[-1].end_layer == 24
    # all layers covered, no gaps
    for i in range(len(plans) - 1):
        assert plans[i].end_layer == plans[i + 1].start_layer
