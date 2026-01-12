import websocket
import json
import threading
import time
import logging
import uuid
import stun
from typing import Optional

from common import RuntimeException, PeerInfo
from common import createUdpSocket, findFreePort
from common import FunctionRegistry
from common.Constants import UDP_CHUNK_SIZE

logger = logging.getLogger(__name__)


class P2PConnectionError(RuntimeException):
    pass


class P2PClient:
    _signalServerHandlers = FunctionRegistry()
    _peerHandlers = FunctionRegistry()

    def __init__(self, signalServer: str):
        self.signalServerAddr = signalServer
        self.signalServerWs = None
        self.signalServerWsThread = None
        self.isConnectedToSignalServer = False
        self.peerInfo = {}

        self.udpPort = findFreePort()
        _, externalIP, externalPort = self._queryStunInfo(self.udpPort)
        self.peerSocket = createUdpSocket(self.udpPort)
        assert self.peerSocket.getsockname()[1] == self.udpPort
        self.info = PeerInfo(externalIP, externalPort, uuid.uuid4())

        self.recvThread = threading.Thread(target=self.recvPeerMessage, daemon=True)
        self.recvThread.start()

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
            logger.info(f"Received UDP message from {addr}: {data}")
            try:
                jsonData = json.loads(data)
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

    @_signalServerHandlers.register("punchNotification")
    def _handlePunchNotification(self, data):
        peerUuid = data["requester"]["uuid"]
        if peerUuid not in self.peerInfo:
            logger.warning(f"Received punch notification from unknown peer {peerUuid}")
            return
        self._sendHolePunchMsg(self.peerInfo[peerUuid])

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
