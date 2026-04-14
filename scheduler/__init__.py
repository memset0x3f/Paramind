"""Scheduler package exports.

Keep package import lightweight so static analyzers can resolve
``scheduler.NodeInventory`` without requiring optional runtime deps.
"""

from importlib import import_module

NodeInventory = import_module("scheduler.NodeInventory")

__all__ = [
    "NodeInventory",
]


def __getattr__(name: str):
    if name in {"ClusterTypes", "ClusterPlanner", "ClusterCoordinator", "DeviceProfile"}:
        return import_module(f"scheduler.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
