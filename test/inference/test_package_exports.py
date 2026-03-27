def test_inference_package_exports_core_runtime_symbols():
    from inference import LocalInferenceEngine, ModelFamily, ShardConfig

    assert LocalInferenceEngine.__name__ == "LocalInferenceEngine"
    assert ModelFamily.QWEN.value == "qwen"
    assert ShardConfig.__name__ == "ShardConfig"
