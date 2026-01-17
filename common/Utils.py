import socket
import struct
import io
import torch
import json
import base64
import time

from common.Constants import UDP_CHUNK_SIZE


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
    s.bind(("", port))
    return s


def sendTorchData(sock, data, uuid, targetAddr, input=True):
    buffer = io.BytesIO()
    torch.save(data, buffer)
    serialized_data = buffer.getvalue()
    b64Str = base64.b64encode(serialized_data).decode()
    chunkSize = UDP_CHUNK_SIZE // 2
    for i in range(0, len(b64Str), chunkSize):
        if i + chunkSize > len(b64Str):
            chunk = b64Str[i:]
        else:
            chunk = b64Str[i : i + chunkSize]

        jsonData = json.dumps(
            {
                "type": "torchInput" if input else "torchOutput",
                "uuid": str(uuid),
                "chunkId": i // chunkSize,
                "nChunk": (len(b64Str) - 1) // chunkSize + 1,
                "obj": chunk,
            }
        ).encode()

        sock.sendto(jsonData, targetAddr)
        time.sleep(0.01)


def deserializeTorchData(objStr: str):
    serialized_data = base64.b64decode(objStr.encode())
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
