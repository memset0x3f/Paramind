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
    Stores both public and internal addresses for multi-path connectivity.
    """

    def __init__(
        self,
        ip: Optional[str] = None,
        port: Optional[int] = None,
        uuid: Optional[UUID] = None,
        isConnected: bool = False,
        internal_ip: Optional[str] = None,
        internal_port: Optional[int] = None,
        timestamp: Optional[float] = None,
    ):
        # Use public address as default (for backward compatibility)
        super().__init__(ip, port)
        self.uuid = uuid
        self.isConnected = isConnected

        self.public_ip = ip
        self.public_port = port
        self.internal_ip = internal_ip
        self.internal_port = internal_port
        self.active_endpoint = None  # 'public', 'internal', or None
        self.timestamp = timestamp

    def isValid(self) -> bool:
        return self.uuid is not None and (
            self.public_ip is not None
            and self.public_port is not None
            or self.internal_ip is not None
            and self.internal_port is not None
        )

    def get_active_address(self) -> tuple[str | None, int | None]:
        """
        Returns the currently active endpoint address (ip, port).
        Falls back to public address if active_endpoint not set.
        """
        if (
            self.active_endpoint == "internal"
            and self.internal_ip
            and self.internal_port
        ):
            return (self.internal_ip, self.internal_port)
        elif self.active_endpoint == "public" and self.public_ip and self.public_port:
            return (self.public_ip, self.public_port)
        elif self.public_ip and self.public_port:
            return (self.public_ip, self.public_port)
        elif self.internal_ip and self.internal_port:
            return (self.internal_ip, self.internal_port)
        else:
            return (self.ip, self.port)  # Fallback to parent Site
