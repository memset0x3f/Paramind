import socket
import torch
from modelscope import snapshot_download
from p2p_core import QwenSlice
from comm_utils import send_data, recv_data

# 配置
MODEL_PATH = snapshot_download("Qwen/Qwen2.5-0.5B-Instruct")
NODE2_PORT = 5002
SPLIT_LAYER = 12
TOTAL_LAYERS = 24 # 0.5B 有 24 层
DEVICE = "cuda:0" # 单机模拟如果显存够可以用同一个卡，不够改成 "cpu" 或 "cuda:1"

print(f"=== NODE 2 (Layers {SPLIT_LAYER}-{TOTAL_LAYERS}) Starting on {DEVICE} ===")
model = QwenSlice(MODEL_PATH, SPLIT_LAYER, TOTAL_LAYERS, device=DEVICE)

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(('0.0.0.0', NODE2_PORT))
server.listen(1)
print(f"Node 2 Listening on port {NODE2_PORT}...")

while True:
    node1_sock, addr = server.accept()
    print(f"Node 1 connected: {addr}")
    
    kv_cache = None
    
    try:
        while True:
            # 1. 接收 Hidden States
            hidden_states_cpu = recv_data(node1_sock)
            if hidden_states_cpu is None: 
                break # 连接断开
            
            hidden_states = hidden_states_cpu.to(DEVICE)
            
            # 2. Node 2 Forward
            with torch.no_grad():
                logits, kv_cache = model.forward(hidden_states, past_key_values=kv_cache)
                
                # 3. Greedy Decoding (取最大概率)
                # logits: [Batch, Seq, Vocab] -> 取最后一个 token
                next_token_logits = logits[:, -1, :]
                next_token_id = torch.argmax(next_token_logits, dim=-1).unsqueeze(0) # [1, 1]
            
            # 4. 发回 Node 1
            send_data(node1_sock, next_token_id.cpu())
            
    except Exception as e:
        print(f"Session Error: {e}")
    finally:
        print("Session ended. Resetting KV Cache.")
        node1_sock.close()