import sys

import pytest

from scheduler.NodeInventory import (
    NodeState,
    _detect_windows_memory_gb,
    _parse_darwin_vm_stat,
    _read_linux_meminfo_kb,
    build_node_state,
)


def test_node_state_round_trip_preserves_new_fields():
    node = NodeState(
        node_id="node-a",
        host="10.0.0.1",
        device_type="cuda",
        total_memory_gb=64.0,
        free_memory_gb=40.0,
        kv_headroom_gb=8.0,
        max_blocks_capacity=24,
        block_latency_ms=12.5,
        loaded_ranges=[(0, 12)],
        online=True,
        latency_ms_to_peers={"node-b": 1.2},
        bandwidth_gbps_to_peers={"node-b": 10.0},
    )

    restored = NodeState.from_dict(node.to_dict())

    assert restored == node


def test_effective_capacity_prefers_explicit_block_capacity():
    node = NodeState(
        node_id="node-a",
        host="10.0.0.1",
        device_type="cpu",
        total_memory_gb=32.0,
        free_memory_gb=20.0,
        kv_headroom_gb=4.0,
        max_blocks_capacity=6,
        block_latency_ms=50.0,
    )

    assert node.effective_capacity_blocks() == 6


def test_read_linux_meminfo_kb_parses_total_and_available(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       8000000 kB\n" "MemAvailable:   4000000 kB\n",
        encoding="utf-8",
    )
    total_kb, avail_kb = _read_linux_meminfo_kb(str(meminfo))
    assert total_kb == 8000000.0
    assert avail_kb == 4000000.0


def test_read_linux_meminfo_kb_falls_back_to_memfree(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       8000000 kB\n" "MemFree:        1000000 kB\n",
        encoding="utf-8",
    )
    total_kb, avail_kb = _read_linux_meminfo_kb(str(meminfo))
    assert total_kb == 8000000.0
    assert avail_kb == 1000000.0


def test_read_linux_meminfo_kb_order_independent(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemAvailable:   4000000 kB\n" "MemTotal:       8000000 kB\n",
        encoding="utf-8",
    )
    total_kb, free_kb = _read_linux_meminfo_kb(str(meminfo))
    assert total_kb == 8000000.0
    assert free_kb == 4000000.0


def test_parse_darwin_vm_stat_uses_available_style_pages():
    vm_stat = (
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                               47485.\n"
        "Pages active:                            915092.\n"
        "Pages inactive:                          911464.\n"
        "Pages speculative:                         3074.\n"
        "Pages purgeable:                          77805.\n"
    )

    page_size, available_pages = _parse_darwin_vm_stat(vm_stat)

    assert page_size == 16384
    assert available_pages == 47485 + 911464 + 3074 + 77805


def test_parse_darwin_vm_stat_accepts_page_size_after_pages():
    vm_stat = (
        "Pages free:                               100.\n"
        "Pages inactive:                           200.\n"
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
    )

    page_size, available_pages = _parse_darwin_vm_stat(vm_stat)

    assert page_size == 16384
    assert available_pages == 100 + 200


def test_parse_darwin_vm_stat_accepts_alternate_page_size_line():
    vm_stat = (
        "Page size: 4096 bytes\n" "Pages free:                               10.\n"
    )

    page_size, available_pages = _parse_darwin_vm_stat(vm_stat)

    assert page_size == 4096
    assert available_pages == 10


@pytest.mark.skipif(
    sys.platform != "win32", reason="GlobalMemoryStatusEx only on Windows"
)
def test_detect_windows_memory_gb_reports_physical_ram():
    total_gb, free_gb = _detect_windows_memory_gb()
    assert total_gb > 0.0
    assert 0.0 <= free_gb <= total_gb


def test_build_node_state_cpu_snapshot_can_report_positive_capacity(monkeypatch):
    monkeypatch.setattr("scheduler.NodeInventory._detect_device_type", lambda: "cpu")
    monkeypatch.setattr(
        "scheduler.NodeInventory._detect_memory_gb", lambda: (36.0, 15.5)
    )
    monkeypatch.setattr(
        "scheduler.NodeInventory.benchmark_block_speed", lambda device_type: 8.0
    )

    node = build_node_state(
        node_id="cpu-node",
        host="cpu.local",
        kv_headroom_gb=1.5,
        loaded_ranges=[],
    )

    assert node.device_type == "cpu"
    assert node.free_memory_gb == 15.5
    assert node.max_blocks_capacity == 14


def test_build_node_state_uses_snapshot_and_benchmark(monkeypatch):
    monkeypatch.setattr("scheduler.NodeInventory._detect_device_type", lambda: "cuda")
    monkeypatch.setattr(
        "scheduler.NodeInventory._detect_memory_gb", lambda: (64.0, 48.0)
    )
    monkeypatch.setattr(
        "scheduler.NodeInventory.benchmark_block_speed", lambda device_type: 80.0
    )

    node = build_node_state(
        node_id="node-a",
        host="10.0.0.1",
        kv_headroom_gb=6.0,
        loaded_ranges=[(0, 8)],
    )

    assert node.device_type == "cuda"
    assert node.total_memory_gb == 64.0
    assert node.free_memory_gb == 48.0
    assert node.kv_headroom_gb == 6.0
    assert node.loaded_ranges == [(0, 8)]
    assert node.block_throughput == 80.0
