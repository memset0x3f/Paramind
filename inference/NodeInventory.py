from __future__ import annotations

from dataclasses import dataclass, field
import ctypes
import math
import platform
import re
import socket
import subprocess

import torch

from inference.ClusterTypes import NodeProfile


def _detect_device_type() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def _parse_darwin_page_size_bytes(line: str) -> int | None:
    line = line.strip()
    m = re.search(r"page size of (\d+) bytes", line, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"Page size:\s*(\d+)\s+bytes", line, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _parse_darwin_vm_stat(text: str) -> tuple[int, int]:
    """Parse vm_stat output; page size may appear before or after Pages* lines."""
    page_size = 4096
    counts: dict[str, int] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        ps = _parse_darwin_page_size_bytes(line)
        if ps is not None:
            page_size = ps
        key_match = re.match(r"Pages ([A-Za-z ]+):\s+(\d+)\.", line)
        if key_match:
            key = key_match.group(1).strip().lower().replace(" ", "_")
            counts[key] = int(key_match.group(2))
    available_pages = (
        counts.get("free", 0)
        + counts.get("inactive", 0)
        + counts.get("speculative", 0)
        + counts.get("purgeable", 0)
    )
    return page_size, available_pages


def _read_linux_meminfo_kb(
    path: str = "/proc/meminfo",
) -> tuple[float | None, float | None]:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return None, None
    total_kb = avail_kb = memfree_kb = None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            total_kb = float(line.split()[1])
        elif line.startswith("MemAvailable:"):
            avail_kb = float(line.split()[1])
        elif line.startswith("MemFree:"):
            memfree_kb = float(line.split()[1])
    if total_kb is None:
        return None, None
    free_kb = avail_kb if avail_kb is not None else memfree_kb
    if free_kb is None:
        free_kb = 0.0
    return total_kb, free_kb


def _detect_linux_memory_gb() -> tuple[float, float]:
    total_kb, avail_kb = _read_linux_meminfo_kb()
    if total_kb is None:
        return 0.0, 0.0
    free_kb = avail_kb if avail_kb is not None else 0.0
    return total_kb / (1024**2), free_kb / (1024**2)


def _detect_darwin_memory_gb() -> tuple[float, float]:
    try:
        total_bytes = int(
            subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return 0.0, 0.0
    total_gb = total_bytes / (1024**3)
    try:
        vm = subprocess.run(
            ["vm_stat"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return total_gb, 0.0
    page_size, available_pages = _parse_darwin_vm_stat(vm)
    free_gb = (available_pages * page_size) / (1024**3)
    return total_gb, min(free_gb, total_gb)


def _detect_windows_memory_gb() -> tuple[float, float]:
    if platform.system() != "Windows":
        return 0.0, 0.0
    from ctypes import wintypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    GlobalMemoryStatusEx = kernel32.GlobalMemoryStatusEx
    GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
    GlobalMemoryStatusEx.restype = wintypes.BOOL
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not GlobalMemoryStatusEx(ctypes.byref(stat)):
        return 0.0, 0.0
    total_gb = stat.ullTotalPhys / (1024**3)
    free_gb = stat.ullAvailPhys / (1024**3)
    return total_gb, min(free_gb, total_gb)


def _detect_host_memory_gb() -> tuple[float, float]:
    system = platform.system()
    if system == "Linux":
        return _detect_linux_memory_gb()
    if system == "Darwin":
        return _detect_darwin_memory_gb()
    if system == "Windows":
        return _detect_windows_memory_gb()
    return 0.0, 0.0


def _detect_memory_gb() -> tuple[float, float]:
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        total = props.total_memory / (1024**3)
        used = torch.cuda.memory_reserved(0) / (1024**3)
        return total, max(total - used, 0.0)
    host_total, host_free = _detect_host_memory_gb()
    if host_total > 0:
        return host_total, max(host_free, 0.0)
    return 0.0, 0.0


def benchmark_block_speed(device_type: str) -> float:
    return 80.0 if device_type == "cuda" else 8.0


@dataclass(frozen=True)
class NodeState:
    node_id: str
    host: str
    device_type: str
    total_memory_gb: float
    free_memory_gb: float
    kv_headroom_gb: float = 0.0
    max_blocks_capacity: int | None = None
    block_latency_ms: float | None = None
    block_throughput: float | None = None
    loaded_ranges: list[tuple[int, int]] = field(default_factory=list)
    online: bool = True
    latency_ms_to_peers: dict[str, float] = field(default_factory=dict)
    bandwidth_gbps_to_peers: dict[str, float] = field(default_factory=dict)

    def effective_free_memory_gb(self) -> float:
        return max(self.free_memory_gb - self.kv_headroom_gb, 0.0)

    def effective_capacity_blocks(self, block_memory_gb: float = 1.0) -> int:
        if self.max_blocks_capacity is not None:
            return max(self.max_blocks_capacity, 0)
        if block_memory_gb <= 0:
            raise ValueError("block_memory_gb must be positive")
        return max(
            int(math.floor(self.effective_free_memory_gb() / block_memory_gb)), 0
        )

    def effective_speed(self) -> float:
        if self.block_throughput is not None and self.block_throughput > 0:
            return self.block_throughput
        if self.block_latency_ms is not None and self.block_latency_ms > 0:
            return 1000.0 / self.block_latency_ms
        baseline = self.effective_free_memory_gb() + (
            100.0 if self.device_type == "cuda" else 5.0
        )
        return max(baseline, 0.1)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "host": self.host,
            "device_type": self.device_type,
            "total_memory_gb": self.total_memory_gb,
            "free_memory_gb": self.free_memory_gb,
            "kv_headroom_gb": self.kv_headroom_gb,
            "max_blocks_capacity": self.max_blocks_capacity,
            "block_latency_ms": self.block_latency_ms,
            "block_throughput": self.block_throughput,
            "loaded_ranges": [list(item) for item in self.loaded_ranges],
            "online": self.online,
            "latency_ms_to_peers": self.latency_ms_to_peers,
            "bandwidth_gbps_to_peers": self.bandwidth_gbps_to_peers,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NodeState":
        return cls(
            node_id=data["node_id"],
            host=data["host"],
            device_type=data["device_type"],
            total_memory_gb=data["total_memory_gb"],
            free_memory_gb=data["free_memory_gb"],
            kv_headroom_gb=data.get("kv_headroom_gb", 0.0),
            max_blocks_capacity=data.get("max_blocks_capacity"),
            block_latency_ms=data.get("block_latency_ms"),
            block_throughput=data.get("block_throughput"),
            loaded_ranges=[tuple(item) for item in data.get("loaded_ranges", [])],
            online=data.get("online", True),
            latency_ms_to_peers=dict(data.get("latency_ms_to_peers", {})),
            bandwidth_gbps_to_peers=dict(data.get("bandwidth_gbps_to_peers", {})),
        )


@dataclass(frozen=True)
class ClusterState:
    nodes: list[NodeState]


def from_local_snapshot(
    node_id: str,
    host: str | None = None,
    kv_headroom_gb: float = 0.0,
    loaded_ranges: list[tuple[int, int]] | None = None,
) -> NodeState:
    device_type = _detect_device_type()
    total_memory_gb, free_memory_gb = _detect_memory_gb()
    host = host or socket.gethostname()
    loaded_ranges = loaded_ranges or []
    return NodeState(
        node_id=node_id,
        host=host,
        device_type=device_type,
        total_memory_gb=total_memory_gb,
        free_memory_gb=free_memory_gb,
        kv_headroom_gb=kv_headroom_gb,
        max_blocks_capacity=max(
            int(math.floor(max(free_memory_gb - kv_headroom_gb, 0.0))), 0
        ),
        block_throughput=benchmark_block_speed(device_type),
        loaded_ranges=loaded_ranges,
    )


def build_node_state(
    node_id: str,
    host: str | None = None,
    kv_headroom_gb: float = 0.0,
    loaded_ranges: list[tuple[int, int]] | None = None,
) -> NodeState:
    return from_local_snapshot(
        node_id=node_id,
        host=host,
        kv_headroom_gb=kv_headroom_gb,
        loaded_ranges=loaded_ranges,
    )


def normalize_node_state(value) -> NodeState:
    if isinstance(value, NodeState):
        return value
    if isinstance(value, NodeProfile):
        return NodeState(
            node_id=value.node_id,
            host=value.host,
            device_type=value.device,
            total_memory_gb=value.total_memory_gb,
            free_memory_gb=value.free_memory_gb,
            kv_headroom_gb=0.0,
            max_blocks_capacity=max(int(math.floor(value.free_memory_gb)), 0),
            block_throughput=max(value.compute_score, 0.1),
            loaded_ranges=list(value.loaded_shards),
        )
    if isinstance(value, dict):
        if "device_type" in value or "loaded_ranges" in value:
            return NodeState.from_dict(value)
        return normalize_node_state(NodeProfile.from_dict(value))
    raise TypeError(f"Cannot normalize node state from {type(value)!r}")
