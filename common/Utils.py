import socket
import struct
import io
import torch
import json
import time
import uuid

from common.Constants import UDP_CHUNK_SIZE


TORCH_PACKET_MAGIC = b"PMB1"


def getLocalIP():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 53))
        local_ip = s.getsockname()[0]
        s.close()
        if local_ip and local_ip != "0.0.0.0":
            return local_ip
    except Exception:
        pass

    # 备选方案：用 localhost
    try:
        return socket.gethostbyname("localhost")
    except Exception:
        pass

    # 如果都失败，返回 localhost
    return "127.0.0.1"


def isPortValid(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("localhost", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def findFreePort():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def createUdpSocket(port: int = 0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", port))
    return s


def sendTorchData(sock, data, sender_uuid, targetAddr, input=True, stream_id=None):
    """
    Send PyTorch tensor data over UDP with chunking.

    Args:
        sock: UDP socket
        data: PyTorch tensor to send
        sender_uuid: Sender UUID
        targetAddr: Target (ip, port) tuple
        input: True for torchInput, False for torchOutput

    Returns:
        Dict with stream_id and packets for resend capability
    """
    buffer = io.BytesIO()
    torch.save(data, buffer)
    serialized_data = buffer.getvalue()
    packet_type = "torchInput" if input else "torchOutput"
    if stream_id is None:
        stream_id = str(uuid.uuid4())
    chunkSize = UDP_CHUNK_SIZE - 256
    nChunk = (len(serialized_data) - 1) // chunkSize + 1

    packets = []  # [(chunkId, packet_bytes), ...]

    for i in range(0, len(serialized_data), chunkSize):
        chunk = serialized_data[i : i + chunkSize]
        header = {
            "type": packet_type,
            "uuid": str(sender_uuid),
            "streamId": stream_id,
            "chunkId": i // chunkSize,
            "nChunk": nChunk,
        }
        header_bytes = json.dumps(header).encode("utf-8")
        packet = (
            TORCH_PACKET_MAGIC
            + struct.pack("!I", len(header_bytes))
            + header_bytes
            + chunk
        )
        packets.append((i // chunkSize, packet))
        sock.sendto(packet, targetAddr)
        time.sleep(0.01)

    return {"stream_id": stream_id, "packets": packets, "stream_type": packet_type}


def deserializeTorchData(serialized_data: bytes):
    buffer = io.BytesIO(serialized_data)
    return torch.load(buffer)


class FunctionRegistry(dict):
    """
    A registry to map strings (e.g., message types) to corresponding functions.
    Functions can be registered to an registry instance with decorators or directly.
    """

    def __init__(self):
        super().__init__()

    def register(self, key: str):
        def wrapper(func):
            self[key] = func
            return func

        return wrapper
