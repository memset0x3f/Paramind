import importlib
import json
import threading

import pytest
import torch

from common.Utils import TORCH_PACKET_MAGIC, sendTorchData
from p2p import PeerSocket


class _RecordingSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, payload, addr):
        self.sent.append((payload, addr))
        return len(payload)


def _make_peer_socket():
    sock = _RecordingSocket()
    peer_socket = PeerSocket(
        sock,
        local_uuid="self-peer",
        peer_lookup=lambda peer_uuid: type(
            "PeerInfoStub",
            (),
            {"get_active_address": lambda self: ("127.0.0.1", 9999)},
        )(),
        logger=None,
        chunk_timeout=0.01,
        max_retries=3,
    )
    return peer_socket, sock


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


def test_send_to_peer_resolves_address_and_sends_tensor():
    peer_socket, raw_sock = _make_peer_socket()
    tensor = torch.arange(1024, dtype=torch.float32)

    result = peer_socket.send_to_peer(tensor, "peer-a", input=True)

    assert result["stream_type"] == "torchInput"
    assert result["stream_id"]
    assert len(raw_sock.sent) == len(result["packets"])


def test_send_to_peer_raises_on_unknown_peer():
    peer_socket, _ = _make_peer_socket()
    peer_socket.peer_lookup = lambda _peer_uuid: None

    with pytest.raises(ValueError, match="Unknown peer"):
        peer_socket.send_to_peer(torch.tensor([1]), "missing-peer", input=True)


def test_timeout_checker_sends_nack_multiple_rounds_before_dropping(monkeypatch):
    peer_socket_module = importlib.import_module("p2p.PeerSocket")
    peer_socket, _ = _make_peer_socket()
    buffer_key = "peer-a:torchInput:stream-1"
    peer_socket.torchDataBuffer[buffer_key] = {
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

    monkeypatch.setattr(peer_socket_module.time, "time", lambda: 10.0)
    monkeypatch.setattr(peer_socket_module.time, "sleep", lambda _seconds: None)

    class _ImmediateThread:
        def __init__(self, target, daemon=True):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(peer_socket_module.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        peer_socket,
        "_send_nack_request",
        lambda src_uuid, stream_type, stream_id, missing_chunks: sent_nacks.append(
            (src_uuid, stream_type, stream_id, list(missing_chunks))
        ),
    )

    peer_socket._start_timeout_checker(buffer_key)

    assert len(sent_nacks) == peer_socket.max_retries
    assert sent_nacks[0] == ("peer-a", "torchInput", "stream-1", [1, 2])
    assert buffer_key not in peer_socket.torchDataBuffer


def test_nack_handler_matches_exact_stream_id(monkeypatch):
    peer_socket, raw_sock = _make_peer_socket()

    peer_socket._pendingTorchSends["peer-a:torchInput:stream-1"] = {
        "packets": [(0, b"packet-a-0"), (1, b"packet-a-1")],
        "target_addr": ("127.0.0.1", 9999),
        "retries": 0,
    }
    peer_socket._pendingTorchSends["peer-a:torchInput:stream-2"] = {
        "packets": [(0, b"packet-b-0"), (1, b"packet-b-1")],
        "target_addr": ("127.0.0.1", 9999),
        "retries": 0,
    }

    sent_payloads = []
    monkeypatch.setattr(
        raw_sock,
        "sendto",
        lambda payload, addr: sent_payloads.append((payload, addr)),
    )

    peer_socket.handle_nack_request(
        {
            "requester_uuid": "peer-a",
            "stream_type": "torchInput",
            "stream_id": "stream-2",
            "missing_chunks": [1],
        }
    )

    assert sent_payloads == [(b"packet-b-1", ("127.0.0.1", 9999))]
    assert peer_socket._pendingTorchSends["peer-a:torchInput:stream-1"]["retries"] == 0
    assert peer_socket._pendingTorchSends["peer-a:torchInput:stream-2"]["retries"] == 1
