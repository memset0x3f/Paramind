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
from common import createUdpSocket, findFreePort
from common import FunctionRegistry
from common.Constants import UDP_CHUNK_SIZE
from common.Utils import sendTorchData, deserializeTorchData
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
        self.info = PeerInfo(externalIP, externalPort, uuid.uuid4())

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
            data = data.decode()
            try:
                jsonData = json.loads(data)
                logger.info(f"Received UDP message from {addr}: {jsonData["type"]}")
            except json.JSONDecodeError:
                logger.warning(f"Received non-JSON UDP message from {addr}: {data}")
                continue
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
                print(
                    f"Step {step}, Hidden States Shape: {hidden_states.shape}. Sending to peer {list(self.peerInfo.values())[0].ip}:{list(self.peerInfo.values())[0].port}..."
                )
                sendTorchData(
                    self.peerSocket,
                    hidden_states.cpu(),
                    self.info.uuid,
                    (
                        list(self.peerInfo.values())[0].ip,
                        list(self.peerInfo.values())[0].port,
                    ),
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
            f"Sending hole punch message to peer {targetPeer.uuid} at {targetPeer.ip}:{targetPeer.port}"
        )
        while self.peerInfo[str(targetPeer.uuid)].isConnected is False:
            # Send UDP packet to target peer's public address
            punchMsg = json.dumps(
                {
                    "type": "punch",
                    "uuid": str(self.info.uuid),
                }
            )
            self.peerSocket.sendto(
                punchMsg.encode(),
                (targetPeer.ip, targetPeer.port),
            )
            time.sleep(0.5)  # Wait before sending the next packet

    @_signalServerHandlers.register("allPeers")
    def _handleAllPeers(self, data):
        peers = data["peers"]
        self.peerInfo = {
            peer["uuid"]: PeerInfo(
                peer["public_address"]["ip"],
                peer["public_address"]["port"],
                uuid.UUID(peer["uuid"]),
            )
            for peer in peers
        }

        logger.info(f"Peers in group: {peers}")

    @_signalServerHandlers.register("newPeer")
    def _handleNewPeer(self, data):
        newPeer = data["peer"]
        self.peerInfo[newPeer["uuid"]] = PeerInfo(
            newPeer["public_address"]["ip"],
            newPeer["public_address"]["port"],
            uuid.UUID(newPeer["uuid"]),
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
        if peerUuid not in self.peerInfo:
            logger.warning(f"Received punch from unknown peer {peerUuid}")
            return
        successMsg = json.dumps(
            {
                "type": "punchSuccess",
                "uuid": str(self.info.uuid),
            }
        )
        self.peerSocket.sendto(
            successMsg.encode(),
            (self.peerInfo[peerUuid].ip, self.peerInfo[peerUuid].port),
        )

    @_peerHandlers.register("punchSuccess")
    def _handlePeerPunchSuccess(self, data):
        peerUuid = data["uuid"]
        if peerUuid not in self.peerInfo:
            logger.warning(f"Received punch success from unknown peer {peerUuid}")
            return
        self.peerInfo[peerUuid].isConnected = True
        logger.info(f"Established connection with peer {peerUuid}")
        print(
            f"Established P2P connection with peer {self.peerInfo[peerUuid].ip}:{self.peerInfo[peerUuid].port}"
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

        sendTorchData(
            self.peerSocket,
            next_token_id.cpu(),
            self.info.uuid,
            (self.peerInfo[peerUuid].ip, self.peerInfo[peerUuid].port),
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
        chunkId = data["chunkId"]
        if uuid not in self.torchDataBuffer:
            self.torchDataBuffer[uuid] = [None] * nChunk
        self.torchDataBuffer[uuid][chunkId] = data["obj"]
        if all(chunk is not None for chunk in self.torchDataBuffer[uuid]):
            # All chunks received, reconstruct the full data
            fullB64Str = "".join(self.torchDataBuffer[uuid])
            tensorData = deserializeTorchData(fullB64Str)
            del self.torchDataBuffer[uuid]  # Clear buffer
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
