import socket
import struct
import pickle
import io
import torch


def send_data(sock, data, target_addr):
    """
    发送任意 Python 对象 (包含 Tensor)。
    协议: [4字节长度头] + [Pickle序列化数据]
    """
    # 使用 pickle 序列化，它能很好地处理 PyTorch Tensor
    buffer = io.BytesIO()
    torch.save(data, buffer)  # 使用 torch.save 比 pickle 更高效且安全
    serialized_data = buffer.getvalue()

    # 发送长度头 (Network byte order, 4 bytes)
    sock.sendto(struct.pack("!I", len(serialized_data)), target_addr)
    # 发送数据
    sock.sendto(serialized_data, target_addr)


def recv_data(sock):
    """
    接收数据
    """
    # 1. 先读 4 字节长度
    raw_len, addr = _recvall(sock, 4)
    if not raw_len:
        return None, None
    msg_len = struct.unpack("!I", raw_len)[0]

    # 2. 读数据体
    data_bytes, addr = _recvall(sock, msg_len)
    if not data_bytes:
        return None, None

    # 3. 反序列化
    buffer = io.BytesIO(data_bytes)
    return (
        torch.load(buffer),
        addr,
    )  # , weights_only=False) # 如果报错 safe，加上 weights_only=False


def _recvall(sock, n):
    """辅助函数：确保读满 n 个字节"""
    data = b""
    while len(data) < n:
        packet, addr = sock.recvfrom(n - len(data))
        if not packet:
            return None, addr
        data += packet
    return data, addr
