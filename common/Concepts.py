from typing import Optional
from uuid import UUID, uuid4


class RuntimeException(Exception):
    """
    Custom exception class to track execution paths.
    """

    def __init__(self, message: str, path: Optional[str] = None):
        super().__init__(message)
        self.execPaths = []
        if path is not None:
            self.execPaths.append(path)
        self.message = message

    def __str__(self):
        return "->".join(self.execPaths) + ": " + self.message

    def appendExecPath(self, path):
        self.execPaths.append(path)


class Site:
    """
    Represents a network site with the form of (IP, port).
    """

    def __init__(self, ip: Optional[str], port: Optional[int]):
        self.ip = ip
        self.port = port

    def isValid(self) -> bool:
        return self.ip is not None and self.port is not None


class PeerInfo(Site):
    """
    Represents information of a peer in the P2P network.
    """

    def __init__(
        self,
        ip: Optional[str] = None,
        port: Optional[int] = None,
        uuid: Optional[UUID] = None,
        isConnected: bool = False,
    ):
        super().__init__(ip, port)
        self.uuid = uuid
        self.isConnected = isConnected

    def isValid(self) -> bool:
        return super().isValid() and self.uuid is not None
