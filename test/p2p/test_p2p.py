from common import RuntimeException
from p2p import P2PClient

import os


def testStunServer():
    udpPort = 54321
    try:
        _, externalIP, externalPort = P2PClient._queryStunInfo(udpPort)
        assert externalIP is not None and externalPort is not None
    except Exception as e:
        assert False, f"STUN server query failed: {e}"


def testSignalServerConnection():
    # For safety reasons, the signaling server URL is taken from an environment variable.
    # Before running this test, ensure you have set the PARAMIND_SIGSERVER environment variable.
    signalServer = os.environ["PARAMIND_SIGSERVER"]
    if not signalServer:
        assert False, "Environment variable PARAMIND_SIGSERVER is not set."
    client = P2PClient(signalServer)
    try:
        client.connect()
    except Exception as e:
        assert False, f"Failed to connect to signaling server: {e}"

    # TODO: Add more tests to verify message sending/receiving once the signaling server is set up for testing.
