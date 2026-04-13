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
from common.Utils import TORCH_PACKET_MAGIC, decodeTorchPacket
from scheduler.NodeInventory import build_node_state
from p2p.PeerSocket import PeerSocket
from inference import QwenSlice
from scheduler.ClusterTypes import NodeProfile

logger = logging.getLogger(__name__)


class P2PConnectionError(RuntimeException):
    pass


class P2PClient:
    _signalServerHandlers = FunctionRegistry()
    _peerHandlers = FunctionRegistry()
    _TORCH_INPUT_QUEUE_SIZE = 64

    def __init__(self, signalServer: str, client_uuid: Optional[uuid.UUID] = None):
        self.signalServerAddr = signalServer
        self.signalServerWs = None
        self.signalServerWsThread = None
        self.isConnectedToSignalServer = False
        self.peerInfo = {}
        self.udpPort = findFreePort()
        _, externalIP, externalPort = self._queryStunInfo(self.udpPort)
        raw_peer_socket = createUdpSocket(self.udpPort)
        assert raw_peer_socket.getsockname()[1] == self.udpPort

        # Get real local IP instead of 0.0.0.0
        internalIP = getLocalIP()
        internalPort = raw_peer_socket.getsockname()[1]
        self.info = PeerInfo(
            externalIP,
            externalPort,
            client_uuid or uuid.uuid4(),
            False,
            internalIP,
            internalPort,
        )
        self.peerSocket = PeerSocket(
            raw_peer_socket,
            local_uuid=str(self.info.uuid),
            peer_lookup=lambda peer_uuid: self.peerInfo.get(peer_uuid),
            logger=logger,
        )

        self.hasRecievedAllPeers = False

        # self.device = "cuda" if torch.cuda.is_available() else "cpu"
        # self.modelPath = snapshot_download("Qwen/Qwen2.5-0.5B-Instruct")
        # self.model = QwenSlice(
        #     self.modelPath, modelLayer[0], modelLayer[1], device=self.device
        # )
        # self.kvCache: Optional[torch.Tensor] = None
        # self.tokenizer = AutoTokenizer.from_pretrained(
        #     self.modelPath, trust_remote_code=True
        # )
        # self.tokenQueue = queue.Queue()
        # self.torchInputQueue = queue.Queue(maxsize=self._TORCH_INPUT_QUEUE_SIZE)

        # self.torchInputWorkerThread = threading.Thread(
        #     target=self._processTorchInput,
        #     daemon=True,
        # )
        # self.torchInputWorkerThread.start()

        self.recvThread = threading.Thread(target=self.recvPeerMessage, daemon=True)
        self.recvThread.start()

        self.runtimeHandlers = {}

    def register_handler(self, message_type: str, handler):
        self.runtimeHandlers[message_type] = handler

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

    def sendToPeer(
        self,
        message,
        peer_uuid: str,
    ):
        self.peerSocket.send_to_peer(message, peer_uuid)

    def broadcast(self, message):
        for peer_uuid in self.peerInfo:
            if peer_uuid == str(self.info.uuid):
                continue
            self.sendToPeer(message, peer_uuid)

    def sendTensorToPeer(
        self,
        tensor: torch.Tensor,
        peer_uuid: str,
        *,
        inference_path,
        inference_id,
        start_node_id,
        input,
        stream_id=None,
    ):
        return self.peerSocket.sendTensor(
            tensor,
            peer_uuid=peer_uuid,
            inference_path=inference_path,
            inference_id=inference_id,
            start_node_id=start_node_id,
            input=input,
            stream_id=stream_id,
        )

    def recvPeerMessage(self):
        # Use max UDP datagram receive size to avoid WinError 10040 when packet
        # slightly exceeds UDP_CHUNK_SIZE due to variable JSON header length.
        recv_buf_size = 65535
        while True:
            data, addr = self.peerSocket.recvfrom(recv_buf_size)
            if data.startswith(TORCH_PACKET_MAGIC):
                jsonData = decodeTorchPacket(data)
                if jsonData is None:
                    continue
                jsonData["_source_addr"] = addr
                logger.info(f"Received UDP message from {addr}: {jsonData['type']}")
                complete = self.peerSocket.handle_torch_datagram(jsonData)
                if complete is None:
                    continue
                complete["_source_addr"] = addr
                self._dispatchPeerHandler(complete)
                continue

            try:
                jsonData = json.loads(data.decode())
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.warning(f"Received non-JSON UDP message from {addr}: {data}")
                continue

            jsonData["_source_addr"] = addr
            logger.info(f"Received UDP message from {addr}: {jsonData['type']}")
            if jsonData["type"] == "nackRequest":
                self.peerSocket.handle_nack_request(jsonData)
                continue
            self._dispatchPeerHandler(jsonData)

    def _dispatchPeerHandler(self, message):
        runtimeHandler = self.runtimeHandlers.get(message["type"])
        if runtimeHandler is not None:
            runtimeHandler(message)
        peerHandler = self._peerHandlers.get(message["type"])
        if peerHandler is not None:
            peerHandler(self, message)

    # def _processTorchInput(self):
    #     while True:
    #         message = self.torchInputQueue.get()
    #         try:
    #             self._handlePeerTorchInput(message)
    #         except Exception:
    #             logger.exception("Failed to process torchInput message")
    #         finally:
    #             self.torchInputQueue.task_done()

    def registerToGroup(self, groupId: str, node_profile: NodeProfile):
        if not self.isConnectedToSignalServer:
            raise P2PConnectionError(
                "Not connected to signaling server.",
                "P2PClient.registerToGroup",
            )
        assert self.signalServerWs is not None

        profile_dict = node_profile.to_dict()

        registerMessage = {
            "type": "register",
            "uuid": str(self.info.uuid),
            "groupId": groupId,
            "publicIp": self.info.ip,
            "publicPort": self.info.port,
            "internalIp": self.info.internal_ip,
            "internalPort": self.info.internal_port,
            "timestamp": time.time(),
            "profile": profile_dict,
        }
        self.signalServerWs.send(json.dumps(registerMessage))
        self.hasRecievedAllPeers = False

    def waitForAllPeerConnection(self, timeout=60):
        start_time = time.time()
        while not self.hasRecievedAllPeers:
            if time.time() - start_time > timeout:
                raise P2PConnectionError(
                    "Timeout while waiting for all peer information.",
                    "P2PClient.waitForAllPeerConnection",
                )
            time.sleep(1)
        while time.time() - start_time < timeout:
            if all(peer.isConnected for peer in self.peerInfo.values()):
                return True
            time.sleep(1)
        raise P2PConnectionError(
            "Timeout while waiting for all peer connections to be established.",
            "P2PClient.waitForAllPeerConnection",
        )

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

    # def infer(self, prompt: str):
    #     messages = [
    #         {"role": "system", "content": "You are a helpful assistant."},
    #         {"role": "user", "content": prompt},
    #     ]
    #     text = self.tokenizer.apply_chat_template(
    #         messages, tokenize=False, add_generation_prompt=True
    #     )
    #     input = self.tokenizer(text, return_tensors="pt").input_ids
    #     input = input.to(self.device)
    #     self.kvCache = None
    #     response = ""
    #     with torch.no_grad():
    #         step = 0
    #         while step < 50:
    #             hidden_states, self.kvCache = self.model.forward(
    #                 input, past_key_values=self.kvCache
    #             )
    #             peer = list(self.peerInfo.values())[0]
    #             active_addr = peer.get_active_address()
    #             # print(
    #             #     f"Step {step}, Hidden States Shape: {hidden_states.shape}. Sending to peer {active_addr[0]}:{active_addr[1]}..."
    #             # )
    #             self.peerSocket.send_to_peer(
    #                 hidden_states.cpu(),
    #                 str(peer.uuid),
    #                 input=True,
    #             )

    #             next_token_id = self.tokenQueue.get()  # LongTensor [1, 1]
    #             # next_token_id 可能是标量或tensor，需要统一处理
    #             if not torch.is_tensor(next_token_id):
    #                 next_token_id = torch.tensor([[next_token_id]], dtype=torch.long)
    #             elif next_token_id.dim() == 0:
    #                 next_token_id = next_token_id.unsqueeze(0).unsqueeze(0)

    #             # 提取标量值用于解码和EOS检查
    #             token_value = next_token_id.squeeze().item()
    #             word = self.tokenizer.decode([token_value])
    #             response += word
    #             print(word, end="", flush=True)  # 流式输出到屏幕

    #             # 检查是否是结束符 (EOS)
    #             eos_token_id = self.tokenizer.eos_token_id
    #             if token_value == eos_token_id:
    #                 print("\nGenerated EOS.")
    #                 break

    #             # 准备下一轮输入：确保next_token_id是[1, 1]形状且在正确的device上
    #             next_token_id = next_token_id.to(self.device)
    #             input = next_token_id
    #             step += 1
    #     # print(f"Response: {response}")

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
                self.peerSocket.send(
                    punchMsg,
                    (targetPeer.public_ip, targetPeer.public_port),
                )
            if targetPeer.internal_ip and targetPeer.internal_port:
                self.peerSocket.send(
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

        self_uuid = str(self.info.uuid)
        if self_uuid not in self.peerInfo:
            self.peerInfo[self_uuid] = PeerInfo(
                ip=self.info.ip,
                port=self.info.port,
                uuid=self.info.uuid,
                isConnected=True,
                internal_ip=self.info.internal_ip,
                internal_port=self.info.internal_port,
            )
        self_peer = self.peerInfo[self_uuid]
        if self_peer.internal_ip and self_peer.internal_port:
            self_peer.active_endpoint = "internal"
        elif self_peer.public_ip and self_peer.public_port:
            self_peer.active_endpoint = "public"
        self.hasRecievedAllPeers = True
        logger.info(f"Peers in group: {peers}")

        # Bridge signaling allPeers event to runtime handlers.
        if self.runtimeHandlers.get("allPeers") is not None:
            self.runtimeHandlers["allPeers"](data)

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

        if self.runtimeHandlers.get("newPeer") is not None:
            self.runtimeHandlers["newPeer"](data)

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
            self.peerSocket.send(successMsg, source_addr)
            logger.info(f"Sent punchSuccess to {source_addr}")
        else:
            # Fallback: try both endpoints
            if peer.public_ip and peer.public_port:
                self.peerSocket.send(successMsg, (peer.public_ip, peer.public_port))
            if peer.internal_ip and peer.internal_port:
                self.peerSocket.send(successMsg, (peer.internal_ip, peer.internal_port))

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

    # @_peerHandlers.register("torchInput")
    # def _handlePeerTorchInput(self, data):
    #     peerUuid = data["uuid"]
    #     tensorData = data["tensor"]
    #     logger.info(f"Received torch data from peer {peerUuid}")

    #     tensorData = tensorData.to(self.device)
    #     with torch.no_grad():
    #         logits, self.kvCache = self.model.forward(
    #             tensorData, past_key_values=self.kvCache
    #         )

    #         # 3. Greedy Decoding (取最大概率)
    #         # logits: [Batch, Seq, Vocab] -> 取最后一个 token
    #         next_token_logits = logits[:, -1, :]
    #         next_token_id = torch.argmax(next_token_logits, dim=-1).unsqueeze(
    #             0
    #         )  # [1, 1]

    #     self.peerSocket.send_to_peer(
    #         next_token_id.cpu(),
    #         peerUuid,
    #         input=False,
    #     )

    # @_peerHandlers.register("torchOutput")
    # def _handlePeerTorchOutput(self, data):
    #     tensorData = data["tensor"]
    #     logger.info(f"Received torch output from peer {data['uuid']}: {tensorData}")
    #     self.tokenQueue.put(tensorData)

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
