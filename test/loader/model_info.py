import torch
from transformers import AutoModelForCausalLM, AutoConfig
from modelscope import snapshot_download
from torchinfo import summary # 这是一个神器

# 1. 下载模型 (Qwen2.5-0.5B)
print("Downloading Qwen2.5-0.5B...")
model_dir = snapshot_download('Qwen/Qwen2.5-7B-Instruct')

# 2. 加载模型
# 注意：为了方便看结构，这里加载到 CPU 即可
model = AutoModelForCausalLM.from_pretrained(
    model_dir, 
    torch_dtype=torch.float16, # 使用半精度模拟真实场景
    device_map="cpu" 
)

config = AutoConfig.from_pretrained(model_dir)

# ==========================================
# 工具 A: 查看关键参数 (P2P传输计算依据)
# ==========================================
h_size = config.hidden_size
num_layers = config.num_hidden_layers
vocab_size = config.vocab_size

print(f"\n=== 模型参数解剖 ===")
print(f"Hidden Size (传输向量维度): {h_size}")
print(f"Total Layers (总层数): {num_layers}")
print(f"Vocab Size: {vocab_size}")

# 计算 P2P 传输量
# 假设 seq_len = 1 (Decode阶段)
# 数据类型 float16 = 2 Bytes
transfer_size_bytes = 1 * h_size * 2 
print(f"\n=== P2P 传输压力预估 ===")
print(f"如果每次传输 1 个 Token 的 Hidden State:")
print(f"数据包大小 = 1 * {h_size} * 2 Bytes = {transfer_size_bytes} Bytes ({transfer_size_bytes/1024:.2f} KB)")
print("这个大小在网络上传输极快！适合做实验。")

# ==========================================
# 工具 B: 直接查看层级名 (用于写切分代码)
# ==========================================
print("\n=== PyTorch 结构名称 (用于定位切分点) ===")
# 只要看前几行就知道层级叫什么了，通常是 model.layers.0 ...
print(model)

# ==========================================
# 工具 C: 使用 torchinfo 查看 tensor 流动形状
# ==========================================
print("\n=== Tensor Flow 可视化 ===")
# 模拟一个输入: Batch=1, Seq_Len=10 (整数)
dummy_input = torch.randint(0, vocab_size, (1, 10), dtype=torch.long)

# 打印详细的层级分析
# depth=2 只显示到 Layer 级别，不显示 Layer 内部的 Attention 细节，方便看整体
summary(model, input_data=dummy_input, depth=2, col_names=["input_size", "output_size", "num_params"])