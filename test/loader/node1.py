import socket
import torch
from modelscope import snapshot_download
from p2p_core import QwenSlice
from comm_utils import send_data, recv_data
from transformers import AutoTokenizer

# 配置
MODEL_PATH = snapshot_download("Qwen/Qwen2.5-0.5B-Instruct")
NODE1_PORT = 5001
NODE2_ADDR = ("127.0.0.1", 5002)
SPLIT_LAYER = 12
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


# 加载 Tokenizer
tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)

# 准备输入
prompt_text = "天空为什么是蓝色的？"
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": prompt_text},
]
text = tokenizer.apply_chat_template(
    messages, tokenize=False, add_generation_prompt=True
)
input_ids = tokenizer(text, return_tensors="pt").input_ids
input_ids = input_ids.to(DEVICE)
print(f"Tokenized Prompt Shape: {input_ids.shape}")

print(f"User: {prompt_text}")
print("Connecting to P2P Inference Network...")

print(f"=== NODE 1 (Layers 0-{SPLIT_LAYER}) Starting on {DEVICE} ===")
model = QwenSlice(MODEL_PATH, 0, SPLIT_LAYER, device=DEVICE)

node1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
node1.bind(("0.0.0.0", NODE1_PORT))
print(f"Node 1 Listening on port {NODE1_PORT}...")

# 初始化 KV Cache
kv_cache = None
curr_input = input_ids

# 生成循环 (限制生成 50 个 token)
with torch.no_grad():
    for step in range(50):
        # === Node 1 Forward ===
        # Prefill 阶段 curr_input 是整句; Decode 阶段是 [1, 1]
        hidden_states, kv_cache = model.forward(curr_input, past_key_values=kv_cache)

        # === 发送给 Node 2 ===
        # 发送 hidden_states (CPU 传输)
        send_data(node1, hidden_states.cpu(), NODE2_ADDR)

        # === 接收 Node 2 的结果 (Next Token ID) ===
        # Node 2 算完后会直接把预测出的 token id 发回来
        next_token_id, _ = recv_data(node1)  # LongTensor [1, 1]
        # === 发送给 Client (用于显示) ===
        # send_data(node1, next_token_id, client_addr)
        word = tokenizer.decode([next_token_id.item()])
        print(word, end="", flush=True)

        # === 准备下一轮 ===
        next_token_id = next_token_id.to(DEVICE)

        # 检查是否是结束符 (EOS)
        if next_token_id.item() in [151643, 151645]:  # Qwen EOS tokens
            print("Generated EOS.")
            break

        curr_input = next_token_id  # Decode 阶段的输入

print("\n")
print("Generation finished. Closing connection.")
