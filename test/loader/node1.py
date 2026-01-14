import socket
import torch
from modelscope import snapshot_download
from p2p_core import QwenSlice
from comm_utils import send_data, recv_data

# 配置
MODEL_PATH = snapshot_download("Qwen/Qwen2.5-0.5B-Instruct")
NODE1_PORT = 5001
NODE2_ADDR = ('127.0.0.1', 5002)
SPLIT_LAYER = 12
DEVICE = "cuda:0"

print(f"=== NODE 1 (Layers 0-{SPLIT_LAYER}) Starting on {DEVICE} ===")
model = QwenSlice(MODEL_PATH, 0, SPLIT_LAYER, device=DEVICE)

# 启动 Server
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(('0.0.0.0', NODE1_PORT))
server.listen(1)
print(f"Node 1 Listening on port {NODE1_PORT}...")

while True:
    client_sock, addr = server.accept()
    print(f"Client connected: {addr}")
    
    # 1. 接收 Prompt (Token IDs)
    input_ids = recv_data(client_sock)
    input_ids = input_ids.to(DEVICE)
    print(f"Received Prompt: {input_ids.shape}")
    
    # 连接 Node 2
    try:
        node2_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        node2_sock.connect(NODE2_ADDR)
    except ConnectionRefusedError:
        print("Error: Node 2 is not online!")
        client_sock.close()
        continue

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
            send_data(node2_sock, hidden_states.cpu())
            
            # === 接收 Node 2 的结果 (Next Token ID) ===
            # Node 2 算完后会直接把预测出的 token id 发回来
            next_token_id = recv_data(node2_sock) # LongTensor [1, 1]
            
            # === 发送给 Client (用于显示) ===
            send_data(client_sock, next_token_id)
            
            # === 准备下一轮 ===
            next_token_id = next_token_id.to(DEVICE)
            
            # 检查是否是结束符 (EOS)
            if next_token_id.item() in [151643, 151645]: # Qwen EOS tokens
                print("Generated EOS.")
                break
                
            curr_input = next_token_id # Decode 阶段的输入
            
    print("Generation finished. Closing connection.")
    node2_sock.close()
    client_sock.close()