from modelscope import snapshot_download

# 下载 Qwen2.5-7B-Instruct (大约 15GB)
# 缓存路径默认在 ~/.cache/modelscope，也可以指定 cache_dir
model_dir = snapshot_download("Qwen/Qwen2.5-7B-Instruct")

print(f"模型已下载到: {model_dir}")
