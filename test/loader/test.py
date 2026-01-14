# run_split.py
import torch
import gc
from transformers import AutoTokenizer, AutoConfig
from modelscope import snapshot_download
from shard import QwenSlice # 导入上面写的类

print("\n=== P2P 大模型切分推理示例 ===")

# =================配置=================
# 使用 ModelScope 国内极速下载

# 注意引号前的 r
model_dir = 'C:/Users/iilka/.cache/modelscope/hub/models/Qwen/Qwen2___5-7B-Instruct'

# Qwen2.5-7B 有 28 层
TOTAL_LAYERS = 28
SPLIT_POINT = 14  # 切分点：前14层 (0-14)，后14层 (14-28)
DEVICE = "cuda:0"

# 准备输入 Prompt
tokenizer = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=True)
prompt_text = "请简述一下什么是P2P大模型推理？"
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": prompt_text}
]
text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
input_ids = tokenizer(text, return_tensors="pt").input_ids.to(DEVICE)

print(f"\n[任务] 输入: {prompt_text}")
print("===============================================")

# ===============================================
# 阶段 1: 运行 Node A (前半部分)
# ===============================================
print("\n>>> 阶段 1: 加载 Node A (Layers 0-14) <<<")
node_a = QwenSlice(model_dir, start_layer=0, end_layer=SPLIT_POINT, device=DEVICE)

print(">>> Node A: 开始推理...")
with torch.no_grad():
    # 输入是 Token IDs，输出是中间层 Hidden States
    intermediate_tensor = node_a.forward(input_ids)

print(f">>> Node A 完成。中间张量形状: {intermediate_tensor.shape}")

# 保存中间结果到 CPU 内存 (模拟网络传输)
# 必须移到 CPU，因为我们要清空 GPU 给 Node B 腾地方
intermediate_data_cpu = intermediate_tensor.to("cpu")

# === 关键：彻底销毁 Node A 释放显存 ===
print(">>> 正在卸载 Node A 以释放显存...")
del node_a
del intermediate_tensor # 删除 GPU 上的引用
gc.collect()
torch.cuda.empty_cache()

current_mem = torch.cuda.memory_allocated(DEVICE) / 1024**3
print(f">>> 显存已清理。当前占用: {current_mem:.2f} GB (应接近 0)")


# ===============================================
# 阶段 2: 运行 Node B (后半部分)
# ===============================================
print("\n>>> 阶段 2: 加载 Node B (Layers 14-28) <<<")
# 此时显存应该是空的，可以安全加载后半部分
node_b = QwenSlice(model_dir, start_layer=SPLIT_POINT, end_layer=TOTAL_LAYERS, device=DEVICE)

print(">>> Node B: 接收中间数据...")
# 把数据从 CPU 搬回 GPU (模拟从网络接收)
intermediate_tensor_gpu = intermediate_data_cpu.to(DEVICE)

print(">>> Node B: 开始推理...")
with torch.no_grad():
    # 输入是 Hidden States，输出是 Logits
    logits = node_b.forward(intermediate_tensor_gpu)

print(">>> Node B 推理完成！")

# ===============================================
# 结果解码
# ===============================================
# 获取最后一个 token 的预测结果
next_token_logits = logits[:, -1, :]
next_token_id = torch.argmax(next_token_logits, dim=-1)
decoded_text = tokenizer.decode(next_token_id)

print("\n===============================================")
print(f"最终预测的下一个字是: '{decoded_text}'")
print("===============================================")

# 清理收尾
del node_b
torch.cuda.empty_cache()