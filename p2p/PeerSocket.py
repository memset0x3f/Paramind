from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Callable, Optional, Protocol

import torch

from common.Constants import TORCH_CHUNK_MAX_RETRIES, TORCH_CHUNK_TIMEOUT
from common.Utils import (
    TORCH_PACKET_MAGIC,
    deserializeTorchData,
    sendTorchData,
)


class PeerSocket:

    class _PeerEndpoint(Protocol):
        def get_active_address(self): ...

    def __init__(
        self,
        sock,
        local_uuid: str,
        peer_lookup: Optional[
            Callable[[str], Optional["PeerSocket._PeerEndpoint"]]
        ] = None,
        logger: Optional[logging.Logger] = None,
        chunk_timeout: float = TORCH_CHUNK_TIMEOUT,
        max_retries: int = TORCH_CHUNK_MAX_RETRIES,
    ):
        self.sock = sock
        self.local_uuid = local_uuid
        self.peer_lookup = peer_lookup
        self.logger = logger or logging.getLogger(__name__)
        self.chunk_timeout = chunk_timeout
        self.max_retries = max_retries

        self.torchDataBuffer = {}
        self._pendingTorchSends = {}
        self._torchDataBufferLock = threading.Lock()
        self._pendingTorchSendsLock = threading.Lock()
        self._completedMessages = queue.Queue()

    def send(self, data, target_addr, *, peer_uuid=None, input=True, stream_id=None):
        if torch.is_tensor(data):
            result = sendTorchData(
                self.sock,
                data,
                sender_uuid=self.local_uuid,
                targetAddr=target_addr,
                input=input,
                stream_id=stream_id,
            )
            self._remember_pending_send(
                peer_uuid=peer_uuid,
                stream_type=result["stream_type"],
                stream_id=result["stream_id"],
                packets=result["packets"],
                target_addr=target_addr,
            )
            return result

        return self.sock.sendto(data, target_addr)

    def send_to_peer(self, data, peer_uuid: str, *, input=True, stream_id=None):
        peer = self._resolve_peer(peer_uuid)
        if peer is None:
            raise ValueError(f"Unknown peer {peer_uuid}")
        target_addr = peer.get_active_address()
        if not target_addr or target_addr[0] is None or target_addr[1] is None:
            raise ValueError(f"Peer {peer_uuid} does not have an active address")
        return self.send(
            data,
            target_addr,
            peer_uuid=peer_uuid,
            input=input,
            stream_id=stream_id,
        )

    def recv(self, timeout: Optional[float] = None):
        return self._completedMessages.get(timeout=timeout)

    def recvfrom(self, bufsize: int):
        return self.sock.recvfrom(bufsize)

    def handle_torch_datagram(self, data: dict):
        nChunk = data["nChunk"]
        sender_uuid = str(data["uuid"])
        stream_type = data["type"]
        stream_id = data.get("streamId") or "legacy"
        buffer_key = f"{sender_uuid}:{stream_type}:{stream_id}"
        chunk_id = data["chunkId"]

        start_timeout_checker = False
        full_bytes = None

        with self._torchDataBufferLock:
            if buffer_key not in self.torchDataBuffer:
                self.torchDataBuffer[buffer_key] = {
                    "chunks": [None] * nChunk,
                    "received_count": 0,
                    "received_at": time.time(),
                    "nChunk": nChunk,
                    "src_uuid": sender_uuid,
                    "stream_type": stream_type,
                    "stream_id": stream_id,
                    "nack_count": 0,
                }
                start_timeout_checker = True

            buffer = self.torchDataBuffer[buffer_key]
            if buffer["chunks"][chunk_id] is None:
                buffer["chunks"][chunk_id] = data["obj"]
                buffer["received_count"] += 1
                buffer["received_at"] = time.time()

            if buffer["received_count"] == nChunk:
                full_bytes = b"".join(buffer["chunks"])
                del self.torchDataBuffer[buffer_key]

        if start_timeout_checker and full_bytes is None:
            self._start_timeout_checker(buffer_key)

        if full_bytes is None:
            return None

        tensor = deserializeTorchData(full_bytes)
        message = {
            "type": stream_type,
            "uuid": sender_uuid,
            "streamId": stream_id,
            "tensor": tensor,
        }
        self._completedMessages.put(message)
        return message

    def handle_nack_request(self, data: dict):
        requester_uuid = data.get("requester_uuid")
        stream_type = data.get("stream_type")
        stream_id = data.get("stream_id")
        missing_chunks = data.get("missing_chunks", [])

        if requester_uuid is None or stream_type is None or stream_id is None:
            self.logger.warning(f"Malformed NACK payload: {data}")
            return

        buffer_key = f"{requester_uuid}:{stream_type}:{stream_id}"

        with self._pendingTorchSendsLock:
            if buffer_key not in self._pendingTorchSends:
                self.logger.warning(f"Received NACK for unknown stream {buffer_key}")
                return

            send_info = self._pendingTorchSends[buffer_key]
            send_info["retries"] += 1

            if send_info["retries"] > self.max_retries:
                self.logger.error(
                    f"Torch stream {buffer_key} exceeded max retries ({self.max_retries}). Giving up."
                )
                del self._pendingTorchSends[buffer_key]
                return

            retry_count = send_info["retries"]
            target_addr = send_info["target_addr"]
            packets = send_info["packets"]

        self.logger.info(
            f"Retransmitting {len(missing_chunks)} chunks for {buffer_key} (retry {retry_count})"
        )

        for chunk_id in missing_chunks:
            if isinstance(chunk_id, int) and 0 <= chunk_id < len(packets):
                _, packet_bytes = packets[chunk_id]
                self.sock.sendto(packet_bytes, target_addr)
                time.sleep(0.01)

    def _remember_pending_send(
        self,
        *,
        peer_uuid: Optional[str],
        stream_type: str,
        stream_id: str,
        packets,
        target_addr,
    ):
        buffer_key = self._make_pending_key(peer_uuid, stream_type, stream_id)
        with self._pendingTorchSendsLock:
            self._pendingTorchSends[buffer_key] = {
                "packets": packets,
                "target_addr": target_addr,
                "retries": 0,
            }

    def _make_pending_key(
        self, peer_uuid: Optional[str], stream_type: str, stream_id: str
    ):
        if peer_uuid is None:
            peer_uuid = "unknown-peer"
        return f"{peer_uuid}:{stream_type}:{stream_id}"

    def _start_timeout_checker(self, buffer_key: str):
        def timeout_checker():
            while True:
                time.sleep(self.chunk_timeout)
                send_nack_payload = None
                drop_stream = False

                with self._torchDataBufferLock:
                    if buffer_key not in self.torchDataBuffer:
                        return

                    buffer = self.torchDataBuffer[buffer_key]
                    if buffer["received_count"] == buffer["nChunk"]:
                        return

                    if time.time() - buffer["received_at"] < self.chunk_timeout:
                        continue

                    if buffer["nack_count"] >= self.max_retries:
                        drop_stream = True
                    else:
                        missing_chunks = [
                            index
                            for index, chunk in enumerate(buffer["chunks"])
                            if chunk is None
                        ]
                        if missing_chunks:
                            buffer["nack_count"] += 1
                            send_nack_payload = {
                                "src_uuid": buffer["src_uuid"],
                                "stream_type": buffer["stream_type"],
                                "stream_id": buffer["stream_id"],
                                "missing_chunks": missing_chunks,
                                "nack_count": buffer["nack_count"],
                            }

                    if drop_stream:
                        self.logger.error(
                            f"Torch stream {buffer_key} exceeded receiver retries ({self.max_retries}). Dropping incomplete stream."
                        )
                        del self.torchDataBuffer[buffer_key]
                        return

                if send_nack_payload is None:
                    continue

                self.logger.warning(
                    f"Torch stream {buffer_key} timeout. Missing chunks: {send_nack_payload['missing_chunks']}. Sending NACK round {send_nack_payload['nack_count']}/{self.max_retries}."
                )
                self._send_nack_request(
                    send_nack_payload["src_uuid"],
                    send_nack_payload["stream_type"],
                    send_nack_payload["stream_id"],
                    send_nack_payload["missing_chunks"],
                )

        thread = threading.Thread(target=timeout_checker, daemon=True)
        thread.start()

    def _send_nack_request(
        self,
        src_uuid: str,
        stream_type: str,
        stream_id: str,
        missing_chunks: list,
    ):
        peer = self._resolve_peer(src_uuid)
        if peer is None:
            self.logger.error(f"Unknown peer {src_uuid}, cannot send NACK")
            return

        target_addr = peer.get_active_address()
        nack_msg = {
            "type": "nackRequest",
            "requester_uuid": str(self.local_uuid),
            "stream_type": stream_type,
            "stream_id": stream_id,
            "missing_chunks": missing_chunks,
        }

        try:
            self.sock.sendto(json.dumps(nack_msg).encode(), target_addr)
            self.logger.info(
                f"Sent NACK for {len(missing_chunks)} missing chunks in {stream_type} to {target_addr}"
            )
        except Exception as exc:
            self.logger.error(f"Failed to send NACK: {exc}")

    def _resolve_peer(self, peer_uuid: str):
        if self.peer_lookup is None:
            return None
        try:
            return self.peer_lookup(peer_uuid)
        except Exception:
            return None
