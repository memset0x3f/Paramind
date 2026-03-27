from __future__ import annotations

import json

import httpx


class CoordinatorClient:
    def __init__(self, base_url: str, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        # Localhost coordinator traffic must bypass ambient proxy settings.
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            trust_env=False,
        )

    def close(self):
        self._client.close()

    def _request_json(self, method: str, path: str, **kwargs):
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            return None

    def register_peer(self, peer: dict):
        return self._request_json("POST", "/api/peers/register", json=peer)

    def heartbeat(self, peer: dict):
        return self._request_json("POST", "/api/peers/heartbeat", json=peer)

    def leave(self, peer_id: str):
        return self._request_json("POST", "/api/peers/leave", json={"id": peer_id})

    def list_peers(self):
        return self._request_json("GET", "/api/peers")

    def publish_event(self, event: dict):
        return self._request_json("POST", "/api/events", json=event)

    def list_events(self, after: int = 0, limit: int | None = None):
        params = {"after": after}
        if limit is not None:
            params["limit"] = limit
        return self._request_json("GET", "/api/events", params=params)
