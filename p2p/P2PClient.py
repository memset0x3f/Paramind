import websocket
import json
import threading
import time
import logging
import uuid
import stun
import torch
import queue
from typing import Optional
from modelscope import snapshot_download
from transformers import AutoTokenizer

from common import RuntimeException, PeerInfo
from common import createUdpSocket, findFreePort, getLocalIP
from common import FunctionRegistry
from common.Constants import UDP_CHUNK_SIZE
from common.Utils import (
    sendTorchData,
    deserializeTorchData,
    TORCH_PACKET_MAGIC,
)
from inference import QwenSlice

logger = logging.getLogger(__name__)


class P2PConnectionError(RuntimeException):
    pass


class P2PClient:
    _signalServerHandlers = FunctionRegistry()
    _peerHandlers = FunctionRegistry()

    def __init__(self, signalServer: str, modelLayer: tuple[int, int]):
        self.signalServerAddr = signalServer
        self.signalServerWs = None
        self.signalServerWsThread = None
        self.isConnectedToSignalServer = False
        self.peerInfo = {}
        self.torchDataBuffer = {}
        self.udpPort = findFreePort()
        _, externalIP, externalPort = self._queryStunInfo(self.udpPort)
        self.peerSocket = createUdpSocket(self.udpPort)
        assert self.peerSocket.getsockname()[1] == self.udpPort

        # Get real local IP instead of 0.0.0.0
        internalIP = getLocalIP()
        internalPort = self.peerSocket.getsockname()[1]
        self.info = PeerInfo(
            externalIP, externalPort, uuid.uuid4(), False, internalIP, internalPort
        )

        self.recvThread = threading.Thread(target=self.recvPeerMessage, daemon=True)
        self.recvThread.start()

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.modelPath = snapshot_download("Qwen/Qwen2.5-0.5B-Instruct")
        self.model = QwenSlice(
            self.modelPath, modelLayer[0], modelLayer[1], device=self.device
        )
        self.kvCache: Optional[torch.Tensor] = None
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.modelPath, trust_remote_code=True
        )
        self.tokenQueue = queue.Queue()

    def connect(self):
        try:
            # Create WebSocket connection to the signaling server
            self.signalServerWs = websocket.WebSocketApp(
                self.signalServerAddr,
                on_open=self._onOpen,
                on_message=self._onMessage,
                on_error=self._onError,
                on_close=self._onClose,
            )
            self.signalServerWsThread = threading.Thread(
                target=self.signalServerWs.run_forever,
                daemon=True,
            )
            self.signalServerWsThread.start()

            for _ in range(200):
                if self.isConnectedToSignalServer:
                    break
                time.sleep(0.5)  # Check connection status every 0.5 seconds
            else:
                raise P2PConnectionError(
                    "Failed to connect to signaling server within timeout period.",
                    "P2PClient.connect",
                )
        except P2PConnectionError as e:
            e.appendExecPath("P2PClient.connect")
            raise e

    def recvPeerMessage(self):
        while True:
            data, addr = self.peerSocket.recvfrom(UDP_CHUNK_SIZE)
            jsonData = None
            if data.startswith(TORCH_PACKET_MAGIC):
                if len(data) < 8:
                    logger.warning(f"Received malformed torch packet from {addr}")
                    continue
                header_len = int.from_bytes(data[4:8], byteorder="big")
                header_end = 8 + header_len
                if len(data) < header_end:
                    logger.warning(f"Received incomplete torch header from {addr}")
                    continue
                try:
                    header = json.loads(data[8:header_end].decode("utf-8"))
                except json.JSONDecodeError:
                    logger.warning(f"Received invalid torch header from {addr}")
                    continue
                jsonData = {
                    "type": header["type"],
                    "uuid": header["uuid"],
                    "chunkId": header["chunkId"],
                    "nChunk": header["nChunk"],
                    "obj": data[header_end:],
                }
            else:
                try:
                    jsonData = json.loads(data.decode())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    logger.warning(f"Received non-JSON UDP message from {addr}: {data}")
                    continue

            # Track the source address for endpoint selection
            jsonData["_source_addr"] = addr
            logger.info(f"Received UDP message from {addr}: {jsonData['type']}")
            if self._peerHandlers.get(jsonData["type"]):
                self._peerHandlers[jsonData["type"]](self, jsonData)

    def registerToGroup(self, groupId: str):
        if not self.isConnectedToSignalServer:
            raise P2PConnectionError(
                "Not connected to signaling server.",
                "P2PClient.registerToGroup",
            )
        assert self.signalServerWs is not None

        registerMessage = {
            "type": "register",
            "uuid": str(self.info.uuid),
            "groupId": groupId,
            "publicIp": self.info.ip,
            "publicPort": self.info.port,
            "internalIp": self.info.internal_ip,
            "internalPort": self.info.internal_port,
        }
        self.signalServerWs.send(json.dumps(registerMessage))

    def holePunchToAllPeers(self):
        for peerUuid, peerInfo in self.peerInfo.items():
            if peerUuid == str(self.info.uuid):
                continue
            self.holePunch(peerInfo)

    def holePunch(self, targetPeer: PeerInfo):
        if not self.isConnectedToSignalServer:
            raise P2PConnectionError(
                "Not connected to signaling server.",
                "P2PClient.holePunch",
            )
        assert self.signalServerWs is not None

        holePunchMessage = {
            "type": "punchRequest",
            "uuid": str(self.info.uuid),
            "target_uuids": [str(targetPeer.uuid)],
        }
        self.signalServerWs.send(json.dumps(holePunchMessage))
        self._sendHolePunchMsg(targetPeer)

    def infer(self, prompt: str):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ]
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        input = self.tokenizer(text, return_tensors="pt").input_ids
        input = input.to(self.device)
        self.kvCache = None
        response = ""
        with torch.no_grad():
            for step in range(50):
                hidden_states, self.kvCache = self.model.forward(
                    input, past_key_values=self.kvCache
                )
                peer = list(self.peerInfo.values())[0]
                active_addr = peer.get_active_address()
                print(
                    f"Step {step}, Hidden States Shape: {hidden_states.shape}. Sending to peer {active_addr[0]}:{active_addr[1]}..."
                )
                sendTorchData(
                    self.peerSocket,
                    hidden_states.cpu(),
                    self.info.uuid,
                    active_addr,
                    input=True,
                )

                next_token_id = self.tokenQueue.get()  # LongTensor [1, 1]
                # === 发送给 Client (用于显示) ===
                # send_data(node1, next_token_id, client_addr)
                word = self.tokenizer.decode([next_token_id.item()])
                response += word
                # === 准备下一轮 ===
                next_token_id = next_token_id.to(self.device)

                # 检查是否是结束符 (EOS)
                if next_token_id.item() in [151643, 151645]:  # Qwen EOS tokens
                    print("Generated EOS.")
                    break

                input = next_token_id  # Decode 阶段的输入
        print(f"Response: {response}")

    def _sendHolePunchMsg(self, targetPeer: PeerInfo):
        logger.info(
            f"Sending hole punch message to peer {targetPeer.uuid} at public {targetPeer.public_ip}:{targetPeer.public_port} and internal {targetPeer.internal_ip}:{targetPeer.internal_port}"
        )
        punchMsg = json.dumps(
            {
                "type": "punch",
                "uuid": str(self.info.uuid),
            }
        ).encode()

        while self.peerInfo[str(targetPeer.uuid)].isConnected is False:
            # Send UDP packet to both public and internal addresses
            if targetPeer.public_ip and targetPeer.public_port:
                self.peerSocket.sendto(
                    punchMsg,
                    (targetPeer.public_ip, targetPeer.public_port),
                )
            if targetPeer.internal_ip and targetPeer.internal_port:
                self.peerSocket.sendto(
                    punchMsg,
                    (targetPeer.internal_ip, targetPeer.internal_port),
                )
            time.sleep(0.5)  # Wait before sending the next packet

    @_signalServerHandlers.register("allPeers")
    def _handleAllPeers(self, data):
        peers = data["peers"]
        self.peerInfo = {}
        for peer in peers:
            pub_addr = peer.get("public_address", {})
            int_addr = peer.get("internal_address", {})
            self.peerInfo[peer["uuid"]] = PeerInfo(
                ip=pub_addr.get("ip"),
                port=pub_addr.get("port"),
                uuid=uuid.UUID(peer["uuid"]),
                internal_ip=int_addr.get("ip"),
                internal_port=int_addr.get("port"),
            )

        logger.info(f"Peers in group: {peers}")

    @_signalServerHandlers.register("newPeer")
    def _handleNewPeer(self, data):
        newPeer = data["peer"]
        pub_addr = newPeer.get("public_address", {})
        int_addr = newPeer.get("internal_address", {})
        self.peerInfo[newPeer["uuid"]] = PeerInfo(
            ip=pub_addr.get("ip"),
            port=pub_addr.get("port"),
            uuid=uuid.UUID(newPeer["uuid"]),
            internal_ip=int_addr.get("ip"),
            internal_port=int_addr.get("port"),
        )
        logger.info(f"New peer joined: {newPeer}")
        self.holePunch(self.peerInfo[newPeer["uuid"]])

    @_signalServerHandlers.register("punchNotification")
    def _handlePunchNotification(self, data):
        peerUuid = data["requester"]["uuid"]
        if peerUuid not in self.peerInfo:
            logger.warning(f"Received punch notification from unknown peer {peerUuid}")
            return
        self._sendHolePunchMsg(self.peerInfo[peerUuid])

    @_peerHandlers.register("punch")
    def _handlePeerPunch(self, data):
        peerUuid = data["uuid"]
        source_addr = data.get("_source_addr")
        if peerUuid not in self.peerInfo:
            logger.warning(f"Received punch from unknown peer {peerUuid}")
            return

        # Detect which endpoint this punch came from (public or internal)
        peer = self.peerInfo[peerUuid]
        if source_addr:
            if source_addr == (peer.public_ip, peer.public_port):
                logger.debug(
                    f"Punch from peer {peerUuid} came from public endpoint {source_addr}"
                )
            elif source_addr == (peer.internal_ip, peer.internal_port):
                logger.debug(
                    f"Punch from peer {peerUuid} came from internal endpoint {source_addr}"
                )

        successMsg = json.dumps(
            {
                "type": "punchSuccess",
                "uuid": str(self.info.uuid),
            }
        ).encode()

        # Send punchSuccess back to the address where punch came from
        if source_addr:
            self.peerSocket.sendto(successMsg, source_addr)
            logger.info(f"Sent punchSuccess to {source_addr}")
        else:
            # Fallback: try both endpoints
            if peer.public_ip and peer.public_port:
                self.peerSocket.sendto(successMsg, (peer.public_ip, peer.public_port))
            if peer.internal_ip and peer.internal_port:
                self.peerSocket.sendto(
                    successMsg, (peer.internal_ip, peer.internal_port)
                )

    @_peerHandlers.register("punchSuccess")
    def _handlePeerPunchSuccess(self, data):
        peerUuid = data["uuid"]
        source_addr = data.get("_source_addr")
        if peerUuid not in self.peerInfo:
            logger.warning(f"Received punch success from unknown peer {peerUuid}")
            return

        peer = self.peerInfo[peerUuid]

        # Auto-select active endpoint based on where punchSuccess came from
        if source_addr:
            if source_addr == (peer.public_ip, peer.public_port):
                peer.active_endpoint = "public"
                logger.info(
                    f"Selected public endpoint for peer {peerUuid}: {source_addr}"
                )
            elif source_addr == (peer.internal_ip, peer.internal_port):
                peer.active_endpoint = "internal"
                logger.info(
                    f"Selected internal endpoint for peer {peerUuid}: {source_addr}"
                )

        peer.isConnected = True
        logger.info(f"Established connection with peer {peerUuid}")
        print(
            f"Established P2P connection with peer {peer.uuid} via {peer.get_active_address()}"
        )

    @_peerHandlers.register("torchInput")
    def _handlePeerTorchInput(self, data):
        peerUuid = data["uuid"]
        tensorData = self._recvTorchData(data)
        if tensorData is None:
            return
        logger.info(f"Received torch data from peer {peerUuid}")

        tensorData = tensorData.to(self.device)
        with torch.no_grad():
            logits, self.kvCache = self.model.forward(
                tensorData, past_key_values=self.kvCache
            )

            # 3. Greedy Decoding (取最大概率)
            # logits: [Batch, Seq, Vocab] -> 取最后一个 token
            next_token_logits = logits[:, -1, :]
            next_token_id = torch.argmax(next_token_logits, dim=-1).unsqueeze(
                0
            )  # [1, 1]

        peer = self.peerInfo[peerUuid]
        active_addr = peer.get_active_address()
        sendTorchData(
            self.peerSocket,
            next_token_id.cpu(),
            self.info.uuid,
            active_addr,
            input=False,
        )

    @_peerHandlers.register("torchOutput")
    def _handlePeerTorchOutput(self, data):
        tensorData = self._recvTorchData(data)
        if tensorData is None:
            return
        logger.info(f"Received torch output from peer {data['uuid']}: {tensorData}")
        self.tokenQueue.put(tensorData)

    def _onOpen(self, ws):
        self.isConnectedToSignalServer = True

    def _onError(self, ws, error):
        self.isConnectedToSignalServer = False
        raise P2PConnectionError(f"WebSocket error: {error}", "P2PClient.on_error")

    def _onClose(self, ws, close_status_code, close_msg):
        self.isConnectedToSignalServer = False
        raise P2PConnectionError(
            f"WebSocket closed with code {close_status_code}, {close_msg}",
            "P2PClient.on_close",
        )

    def _onMessage(self, ws, message):
        data = json.loads(message)
        logger.debug(f"Received message: {data}")
        if self._signalServerHandlers.get(data["type"]):
            self._signalServerHandlers[data["type"]](self, data)

    def _recvTorchData(self, data):
        nChunk = data["nChunk"]
        uuid = str(data["uuid"])
        streamType = data["type"]
        bufferKey = f"{uuid}:{streamType}"
        chunkId = data["chunkId"]
        if bufferKey not in self.torchDataBuffer:
            self.torchDataBuffer[bufferKey] = [None] * nChunk
        self.torchDataBuffer[bufferKey][chunkId] = data["obj"]
        if all(chunk is not None for chunk in self.torchDataBuffer[bufferKey]):
            # All chunks received, reconstruct the full data
            fullBytes = b"".join(self.torchDataBuffer[bufferKey])
            tensorData = deserializeTorchData(fullBytes)
            del self.torchDataBuffer[bufferKey]  # Clear buffer
            return tensorData
        return None

    @staticmethod
    def _queryStunInfo(port):
        """
        Query and set the client's own information from the STUN server.
        """
        # TODO: Support specifying different STUN servers in config file and request each after another if one fails.
        # return stun.get_ip_info(
        #     stun_host="stun.l.google.com", stun_port=19302, source_port=port
        # )

        nat, externalIP, externalPort = None, None, None

        while nat is None or externalIP is None or externalPort is None:
            try:
                nat, externalIP, externalPort = stun.get_ip_info(
                    stun_host="stun.12voip.com", stun_port=3478, source_port=port
                )
            except Exception as e:
                logger.warning(f"STUN server query failed: {e}. Retrying...")
                time.sleep(1)

        return nat, externalIP, externalPort
