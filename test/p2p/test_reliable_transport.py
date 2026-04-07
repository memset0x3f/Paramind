import importlib
import json
import threading
from types import SimpleNamespace

import torch

from common.Utils import TORCH_PACKET_MAGIC, sendTorchData
from p2p import P2PClient


class _RecordingSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, payload, addr):
        self.sent.append((payload, addr))
        return len(payload)


def _make_client_stub():
    client = P2PClient.__new__(P2PClient)
    client.peerInfo = {}
    client.torchDataBuffer = {}
    client._pendingTorchSends = {}
    client._torchDataBufferLock = threading.Lock()
    client._pendingTorchSendsLock = threading.Lock()
    client.peerSocket = _RecordingSocket()
    client.info = SimpleNamespace(uuid="self-peer")
    return client


def test_send_torch_data_generates_stream_id_and_keeps_header_consistent():
    sock = _RecordingSocket()
    tensor = torch.arange(2048, dtype=torch.float32)

    result = sendTorchData(
        sock,
        tensor,
        sender_uuid="sender-peer",
        targetAddr=("127.0.0.1", 9999),
        input=True,
    )

    assert result["stream_id"]
    assert result["stream_type"] == "torchInput"
    assert len(result["packets"]) >= 1
    assert len(sock.sent) == len(result["packets"])

    seen_stream_ids = set()
    for payload, addr in sock.sent:
        assert addr == ("127.0.0.1", 9999)
        assert payload.startswith(TORCH_PACKET_MAGIC)
        header_len = int.from_bytes(payload[4:8], byteorder="big")
        header = json.loads(payload[8 : 8 + header_len].decode("utf-8"))
        seen_stream_ids.add(header["streamId"])
        assert header["uuid"] == "sender-peer"
        assert header["type"] == "torchInput"

    assert seen_stream_ids == {result["stream_id"]}


def test_timeout_checker_sends_nack_multiple_rounds_before_dropping(monkeypatch):
    p2p_client_module = importlib.import_module("p2p.P2PClient")
    client = _make_client_stub()
    buffer_key = "peer-a:torchInput:stream-1"
    client.torchDataBuffer[buffer_key] = {
        "chunks": [b"chunk-0", None, None],
        "received_count": 1,
        "received_at": 0.0,
        "nChunk": 3,
        "src_uuid": "peer-a",
        "stream_type": "torchInput",
        "stream_id": "stream-1",
        "nack_count": 0,
    }

    sent_nacks = []

    monkeypatch.setattr(p2p_client_module.time, "time", lambda: 10.0)
    monkeypatch.setattr(p2p_client_module.time, "sleep", lambda _seconds: None)

    class _ImmediateThread:
        def __init__(self, target, daemon=True):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(p2p_client_module.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        client,
        "_sendNackRequest",
        lambda src_uuid, stream_type, stream_id, missing_chunks: sent_nacks.append(
            (src_uuid, stream_type, stream_id, list(missing_chunks))
        ),
    )

    client._startTorchStreamTimeout(buffer_key)

    assert len(sent_nacks) == p2p_client_module.TORCH_CHUNK_MAX_RETRIES
    assert sent_nacks[0] == ("peer-a", "torchInput", "stream-1", [1, 2])
    assert buffer_key not in client.torchDataBuffer


def test_nack_handler_matches_exact_stream_id(monkeypatch):
    client = _make_client_stub()
    client.peerInfo["peer-a"] = SimpleNamespace(
        get_active_address=lambda: ("127.0.0.1", 9999)
    )

    client._pendingTorchSends["peer-a:torchInput:stream-1"] = {
        "packets": [(0, b"packet-a-0"), (1, b"packet-a-1")],
        "target_addr": ("127.0.0.1", 9999),
        "retries": 0,
    }
    client._pendingTorchSends["peer-a:torchInput:stream-2"] = {
        "packets": [(0, b"packet-b-0"), (1, b"packet-b-1")],
        "target_addr": ("127.0.0.1", 9999),
        "retries": 0,
    }

    sent_payloads = []
    monkeypatch.setattr(
        client.peerSocket,
        "sendto",
        lambda payload, addr: sent_payloads.append((payload, addr)),
    )

    client._handleNackRequest(
        {
            "requester_uuid": "peer-a",
            "stream_type": "torchInput",
            "stream_id": "stream-2",
            "missing_chunks": [1],
        }
    )

    assert sent_payloads == [(b"packet-b-1", ("127.0.0.1", 9999))]
    assert client._pendingTorchSends["peer-a:torchInput:stream-1"]["retries"] == 0
    assert client._pendingTorchSends["peer-a:torchInput:stream-2"]["retries"] == 1
