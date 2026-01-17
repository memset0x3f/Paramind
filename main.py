from common import RuntimeException
from p2p import P2PClient

import os
import sys
import logging
import time
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--log", type=str, default="paramind.log", help="Log file path")
args = parser.parse_args()

logging.basicConfig(
    filename=args.log,
    format="%(asctime)s  %(filename)s : %(levelname)s  %(message)s",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)
clientId = 1
if args.log != "1.log":
    clientId = 2

project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

signalServer = os.environ["PARAMIND_SIGSERVER"]
if not signalServer:
    assert False, "Environment variable PARAMIND_SIGSERVER not set."

client = P2PClient(signalServer, (0, 12) if clientId == 1 else (12, 24))
print(f"Client {client.info.uuid} info: IP={client.info.ip}, Port={client.info.port}")
# print(client.udpPort)
client.connect()
client.registerToGroup("test-group-001")
print("Registered to signaling server.")
print("Client ID:", clientId)
# sock = client.peerSocket
# for _ in range(100):
#     sock.sendto(
#         b"Hello",
#         ("20.9.128.1", 12345),
#     )
#     time.sleep(1)
# res = sock.recvfrom(1024)
# print("Received:", res[0].decode())
# time.sleep(2)
# client.holePunchToAllPeers()

while len(client.peerInfo) < 1 or (not list(client.peerInfo.values())[0].isConnected):
    time.sleep(1)

if clientId == 1:
    print("Starting inference...")
    prompt = "H"
    client.infer(prompt)
else:
    while True:
        time.sleep(1)
