import websocket
import json
import threading
import time
import logging

from common import RuntimeException

logger = logging.getLogger(__name__)


class P2PConnectionError(RuntimeException):
    pass


class P2PClient:
    def __init__(self, signalServer):
        self.signalServer = signalServer
        self.signalServerWs = None
        self.signalServerWsThread = None
        self.isConnectedToSignalServer = False
        self.selfIP = None
        self.selfPort = None
        self.peerIP = None
        self.peerPort = None

    def connect(self):
        try:
            # Create WebSocket connection to the signaling server
            self.signalServerWs = websocket.WebSocketApp(
                self.signalServer,
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

    def _onOpen(self, ws):
        self.isConnectedToSignalServer = True

    def _onError(self, ws, error):
        raise P2PConnectionError(f"WebSocket error: {error}", "P2PClient.on_error")

    def _onClose(self, ws, close_status_code, close_msg):
        raise P2PConnectionError(
            f"WebSocket closed with code {close_status_code}, {close_msg}",
            "P2PClient.on_close",
        )

    def _onMessage(self, ws, message):
        data = json.loads(message)
        logger.debug(f"Received message: {data}")
