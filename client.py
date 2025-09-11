#!/usr/bin/env python3
"""
P2P Hole Punching Client - Connects to signaling server via WebSocket
"""

import websocket
import json
import threading
import time
import socket
import logging
from urllib.parse import urlparse
import requests
import argparse

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class P2PClient:
    def __init__(self, server_url):
        self.server_url = server_url
        self.ws = None
        self.client_id = None
        self.partner_id = None
        self.is_connected = False
        self.paired = False

        # UDP hole punching related
        self.udp_socket = None
        self.local_ip = "0.0.0.0"
        self.local_port = 0
        self.partner_address = None

        # Message callbacks
        self.message_handlers = {
            "welcome": self._handle_welcome,
            "paired": self._handle_paired,
            "punch": self._handle_punch,
            "message": self._handle_message,
            "partner_left": self._handle_partner_left,
            "waiting": self._handle_waiting,
        }

    def connect(self):
        """Connect to signaling server"""
        try:
            logger.info(f"Connecting to signaling server: {self.server_url}")

            # Create WebSocket connection
            self.ws = websocket.WebSocketApp(
                self.server_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            # Start WebSocket thread
            self.ws_thread = threading.Thread(target=self.ws.run_forever)
            self.ws_thread.daemon = True
            self.ws_thread.start()

            # Wait for connection to establish
            for _ in range(200):
                if self.is_connected:
                    break
                time.sleep(0.5)

            if self.is_connected:
                logger.info("Successfully connected to signaling server")
                return True
            else:
                logger.error("Connection timeout")
                return False

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from server"""
        if self.ws:
            self.ws.close()
        self.is_connected = False
        self.paired = False
        logger.info("Disconnected")

    def request_pair(self):
        """Request pairing"""
        if self.is_connected:
            self._send_message({"type": "pair"})
            logger.info("Pairing request sent")
        else:
            logger.warning("Not connected to server")

    def send_punch_info(self):
        """Send hole punching information"""
        if not self.paired:
            logger.warning("Not paired yet, cannot send punch info")
            return

        # Get public IP (simplified version, should use STUN protocol in reality)
        public_ip = self._get_public_ip()
        if not public_ip:
            logger.warning("Failed to get public IP")
            return

        # Create UDP socket for hole punching
        if not self._setup_udp_socket():
            logger.warning("Failed to create UDP socket")
            return

        punch_info = {
            "type": "punch",
            "address": {"ip": public_ip, "port": self.local_port},
        }

        self._send_message(punch_info)
        logger.info(f"Punch info sent: {public_ip}:{self.local_port}")

    def send_message(self, text):
        """Send text message"""
        if self.paired:
            self._send_message({"type": "message", "text": text})
            logger.info(f"Message sent: {text}")
        else:
            logger.warning("Not paired yet, cannot send message")

    def start_punching(self, target_ip, target_port):
        """Start sending punch packets to target address"""
        self.partner_address = (target_ip, target_port)
        logger.info(f"Start sending punch packets to {target_ip}:{target_port}")

        # Start punching thread
        punch_thread = threading.Thread(target=self._punch_loop)
        punch_thread.daemon = True
        punch_thread.start()

    def _setup_udp_socket(self):
        """Create UDP socket"""
        try:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_socket.bind((self.local_ip, 0))  # Random port
            self.local_port = self.udp_socket.getsockname()[1]

            # Set non-blocking
            self.udp_socket.setblocking(False)

            # Start receive thread
            receive_thread = threading.Thread(target=self._receive_loop)
            receive_thread.daemon = True
            receive_thread.start()

            logger.info(f"UDP socket created: {self.local_ip}:{self.local_port}")
            return True

        except Exception as e:
            logger.error(f"Failed to create UDP socket: {e}")
            return False

    def _punch_loop(self):
        """Continuously send punch packets"""
        if not self.partner_address or not self.udp_socket:
            return

        punch_count = 0
        while self.paired and punch_count < 20:  # Send 20 punch packets
            try:
                message = f"PUNCH#{punch_count}#FROM:{self.client_id}"
                self.udp_socket.sendto(message.encode("utf-8"), self.partner_address)
                logger.debug(
                    f"Sending punch packet #{punch_count} to {self.partner_address}"
                )
                punch_count += 1
                time.sleep(0.5)  # Send every 0.5 seconds

            except Exception as e:
                logger.error(f"Failed to send punch packet: {e}")
                break

    def _receive_loop(self):
        """Receive UDP data"""
        while self.udp_socket:
            try:
                data, addr = self.udp_socket.recvfrom(1024)
                message = data.decode("utf-8")
                logger.info(f"Received message from {addr}: {message}")

                # If it's a punch packet response, hole punching succeeded
                if message.startswith("PUNCH#"):
                    logger.info(
                        f"🎉 Hole punching successful! Direct connection established with {addr}"
                    )

            except BlockingIOError:
                # No data, continue waiting
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Error receiving data: {e}")
                break

    def _get_public_ip(self):
        """Get public IP (simplified implementation)"""
        try:
            response = requests.get("https://httpbin.org/ip", timeout=5)
            return response.json().get("origin", "unknown")
        except:
            try:
                # Fallback option
                response = requests.get("https://api.ipify.org", timeout=5)
                return response.text
            except:
                return None

    def _send_message(self, data):
        """Send WebSocket message"""
        if self.ws and self.is_connected:
            try:
                self.ws.send(json.dumps(data))
            except Exception as e:
                logger.error(f"Failed to send message: {e}")

    # WebSocket event handlers
    def _on_open(self, ws):
        logger.info("WebSocket connection established")
        self.is_connected = True

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            logger.info(f"Received server message: {msg_type}")

            # Call corresponding message handler
            handler = self.message_handlers.get(msg_type)
            if handler:
                handler(data)
            else:
                logger.warning(f"Unknown message type: {msg_type}")

        except json.JSONDecodeError:
            logger.error(f"Message parsing failed: {message}")

    def _on_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logger.info("WebSocket connection closed")
        self.is_connected = False
        self.paired = False

    # Message handlers
    def _handle_welcome(self, data):
        self.client_id = data["clientId"]
        logger.info(f"Welcome! My client ID: {self.client_id}")

    def _handle_paired(self, data):
        self.partner_id = data["partner"]
        self.paired = True
        logger.info(f"Pairing successful! Partner: {self.partner_id}")
        logger.info("Ready to start hole punching")

    def _handle_punch(self, data):
        address = data["address"]
        logger.info(
            f"Received punch info from {data['from']}: {address['ip']}:{address['port']}"
        )

        # Start sending punch packets to partner address
        self.start_punching(address["ip"], address["port"])

    def _handle_message(self, data):
        logger.info(f"Received message from {data['from']}: {data['text']}")

    def _handle_partner_left(self, data):
        logger.warning("Partner disconnected")
        self.paired = False
        self.partner_id = None

    def _handle_waiting(self, data):
        logger.info("Waiting for pairing...")


# Usage example
def main():
    argParser = argparse.ArgumentParser(description="P2P Hole Punching Client")
    argParser.add_argument(
        "--server",
        type=str,
        help="WebSocket signaling server URL",
    )
    # Create client instance
    # Replace with your Railway app URL
    args = argParser.parse_args()
    if args.server:
        client = P2PClient(args.server)
    else:
        raise ValueError("Please provide the signaling server URL using --server")

    # Connect to server
    if not client.connect():
        return

    try:
        # Wait for welcome message
        time.sleep(1)

        # Request pairing
        client.request_pair()

        # Wait for pairing
        logger.info("Waiting for pairing...")
        while not client.paired:
            time.sleep(1)

        # Send punch info after successful pairing
        time.sleep(1)  # Give both sides some preparation time
        client.send_punch_info()

        # Wait for hole punching process
        logger.info("Hole punching in progress, waiting 10 seconds...")
        time.sleep(10)

        # Send test message
        client.send_message("Hello from Python client!")

        # Keep running
        logger.info("Press Ctrl+C to exit")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("User interrupted")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
