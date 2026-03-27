from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ShardRecord:
    shard_key: tuple[int, int]
    role: str
    state: str
    shard_obj: object | None = None
    active_owner: bool = False
    pending_unload: bool = False
    inflight_requests: int = 0
    last_prepare_ts: float | None = None
    last_commit_ts: float | None = None


class ShardRegistry:
    def __init__(self):
        self._records: dict[tuple[int, int], ShardRecord] = {}

    def get(self, shard_key: tuple[int, int]) -> ShardRecord | None:
        return self._records.get(shard_key)

    def put(self, record: ShardRecord) -> None:
        self._records[record.shard_key] = record

    def remove(self, shard_key: tuple[int, int]) -> None:
        self._records.pop(shard_key, None)

    def all_records(self) -> list[ShardRecord]:
        return list(self._records.values())
