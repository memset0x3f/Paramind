import socket
import torch
from transformers import AutoTokenizer
from modelscope import snapshot_download
from comm_utils import send_data, recv_data
import sys

MODEL_PATH = snapshot_download("Qwen/Qwen2.5-0.5B-Instruct")
NODE1_ADDR = ("127.0.0.1", 5001)

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

print(f"User: {prompt_text}")
print("Connecting to P2P Inference Network...")

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.connect(NODE1_ADDR)

# 1. 发送 Prompt
send_data(client, input_ids)

print("Assistant: ", end="", flush=True)

# 2. 循环接收 Token
while True:
    token_id_tensor = recv_data(client)
    if token_id_tensor is None:
        break

    token_id = token_id_tensor.item()
    word = tokenizer.decode([token_id])

    print(word, end="", flush=True)

print("\n\n[Finished]")
client.close()
