#!/usr/bin/env python3
"""FastAPI backend for the ParaMind desktop app."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse


def _bootstrap_pythonpath():
    python_root = Path(__file__).resolve().parent
    candidates = [python_root]
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if entry:
            candidates.append(Path(entry))
    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate_str and candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)


if __package__ in {None, ""}:
    _bootstrap_pythonpath()
    from app.config import build_settings
    from app.database import create_sqlite_engine, init_db
    from app.services import DesktopAppService
else:
    from .app.config import build_settings
    from .app.database import create_sqlite_engine, init_db
    from .app.services import DesktopAppService


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def create_app(test_mode: bool = False, settings_overrides: Optional[dict] = None):
    settings = build_settings(test_mode=test_mode, overrides=settings_overrides)
    engine = create_sqlite_engine(settings.database_path)
    init_db(engine)
    service = DesktopAppService(
        settings,
        engine,
        p2p_transport=(settings_overrides or {}).get("p2p_transport"),
    )
    service.ensure_initialized()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.worker_task = asyncio.create_task(service.worker_loop())
        app.state.heartbeat_task = asyncio.create_task(service.heartbeat_loop())
        app.state.preload_task = (
            None
            if settings.test_mode
            else asyncio.create_task(service.inference.preload())
        )
        yield
        service.stop_event.set()
        app.state.worker_task.cancel()
        app.state.heartbeat_task.cancel()
        if app.state.preload_task is not None:
            app.state.preload_task.cancel()
        service.mark_self_offline()

    app = FastAPI(title="ParaMind Desktop Backend", lifespan=lifespan)
    app.state.settings = settings
    app.state.service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {"status": "healthy"}

    @app.get("/api/bootstrap")
    def bootstrap():
        return service.bootstrap_payload()

    @app.get("/api/status")
    def status():
        bootstrap_data = service.bootstrap_payload()
        return {
            "model_id": bootstrap_data["model"]["model_id"],
            "mode": (
                "distributed"
                if bootstrap_data["network"]["peer_count"] > 1
                else "local"
            ),
            "device": bootstrap_data["model"]["device"],
            "family": bootstrap_data["model"]["family"],
        }

    @app.get("/api/conversations")
    def conversations():
        return service.list_conversations()

    @app.get("/api/dm/requests")
    def dm_requests():
        return service.list_dm_requests()

    @app.get("/api/group/invitations")
    def group_invitations():
        return service.list_group_invitations()

    @app.post("/api/dm/requests", status_code=201)
    def create_dm_request(payload: dict):
        target_peer_id = str(payload.get("target_peer_id") or "").strip()
        if not target_peer_id:
            raise HTTPException(status_code=400, detail="target_peer_id is required")
        result = service.create_dm_request(target_peer_id)
        if result is None:
            raise HTTPException(status_code=400, detail="unable to create dm request")
        return result

    @app.post("/api/group/invitations", status_code=201)
    def create_group_invitations(payload: dict):
        title = str(payload.get("title") or "").strip()
        conversation_id = str(payload.get("conversation_id") or "").strip() or None
        target_peer_ids = [
            str(item).strip()
            for item in list(payload.get("target_peer_ids") or [])
            if str(item).strip()
        ]
        if not target_peer_ids:
            raise HTTPException(status_code=400, detail="target_peer_ids are required")
        if not title and not conversation_id:
            raise HTTPException(
                status_code=400, detail="title or conversation_id is required"
            )
        created = service.create_group_invitations(
            title, target_peer_ids, conversation_id=conversation_id
        )
        if created is None:
            raise HTTPException(
                status_code=400, detail="unable to create group invitations"
            )
        return created

    @app.post("/api/dm/requests/{request_id}/accept")
    def accept_dm_request(request_id: str):
        request = service.respond_dm_request(request_id, accept=True)
        if request is None:
            raise HTTPException(status_code=404, detail="dm request not found")
        return request

    @app.post("/api/dm/requests/{request_id}/reject")
    def reject_dm_request(request_id: str):
        request = service.respond_dm_request(request_id, accept=False)
        if request is None:
            raise HTTPException(status_code=404, detail="dm request not found")
        return request

    @app.post("/api/group/invitations/{invitation_id}/accept")
    def accept_group_invitation(invitation_id: str):
        invitation = service.respond_group_invitation(invitation_id, accept=True)
        if invitation is None:
            raise HTTPException(status_code=404, detail="group invitation not found")
        return invitation

    @app.post("/api/group/invitations/{invitation_id}/reject")
    def reject_group_invitation(invitation_id: str):
        invitation = service.respond_group_invitation(invitation_id, accept=False)
        if invitation is None:
            raise HTTPException(status_code=404, detail="group invitation not found")
        return invitation

    @app.post("/api/conversations", status_code=201)
    def create_conversation(payload: dict):
        title = str(payload.get("title") or "").strip()
        kind = str(payload.get("kind") or "group").strip()
        participant_ids = list(payload.get("participant_ids") or [])
        if not title:
            raise HTTPException(status_code=400, detail="title is required")
        return service.create_conversation(
            title=title, kind=kind, participant_ids=participant_ids
        )

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str):
        conversation = service.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conversation

    @app.post("/api/conversations/{conversation_id}/leave")
    def leave_conversation(conversation_id: str):
        payload = service.leave_dm_conversation(conversation_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return payload

    @app.post("/api/conversations/{conversation_id}/close")
    def close_conversation(conversation_id: str):
        payload = service.close_conversation(conversation_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return payload

    @app.get("/api/conversations/{conversation_id}/messages")
    def messages(conversation_id: str):
        conversation = service.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return service.list_messages(conversation_id)

    @app.post("/api/conversations/{conversation_id}/messages", status_code=201)
    def create_message(conversation_id: str, payload: dict):
        role = str(payload.get("role") or "user").strip()
        content = str(payload.get("content") or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="content is required")
        message = service.create_message(
            conversation_id=conversation_id, role=role, content=content
        )
        if message is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return message

    @app.post("/api/ai/drafts", status_code=201)
    async def create_ai_draft(payload: dict):
        conversation_id = str(payload.get("conversation_id") or "").strip()
        prompt = str(payload.get("prompt") or "").strip()
        source_message_id = str(payload.get("source_message_id") or "").strip()
        if not conversation_id or (not prompt and not source_message_id):
            raise HTTPException(
                status_code=400,
                detail="conversation_id and either prompt or source_message_id are required",
            )
        result = service.create_ai_draft(
            conversation_id,
            prompt=prompt or None,
            source_message_id=source_message_id or None,
        )
        if result is None:
            raise HTTPException(
                status_code=404, detail="conversation or source message not found"
            )
        if service.settings.test_mode:
            import asyncio

            asyncio.create_task(service._run_job(result["job"]["id"]))
        else:
            await service.enqueue_job(result["job"]["id"])
        return {"draft": result["assistant_message"], "job": result["job"]}

    @app.post("/api/conversations/{conversation_id}/ai-draft", status_code=201)
    async def create_ai_draft_legacy(conversation_id: str, payload: dict):
        payload = dict(payload or {})
        payload["conversation_id"] = conversation_id
        return await create_ai_draft(payload)

    @app.patch("/api/ai/drafts/{draft_id}")
    def update_ai_draft(draft_id: str, payload: dict):
        content = str(payload.get("content") or "")
        draft = service.update_ai_draft(draft_id, content)
        if draft is None:
            raise HTTPException(status_code=404, detail="ai draft not found")
        return draft

    @app.delete("/api/ai/drafts/{draft_id}")
    def delete_ai_draft(draft_id: str):
        deleted = service.delete_ai_draft(draft_id)
        if deleted is None:
            raise HTTPException(status_code=404, detail="ai draft not found")
        return deleted

    @app.post("/api/ai/drafts/{draft_id}/send", status_code=201)
    def send_ai_draft(draft_id: str):
        message = service.send_ai_draft(draft_id)
        if message is None:
            raise HTTPException(status_code=404, detail="ai draft not found")
        return message

    @app.get("/api/conversations/{conversation_id}/stream")
    async def stream_conversation_events(
        conversation_id: str,
        after: int = Query(0, ge=0),
        limit: Optional[int] = Query(None, ge=1),
    ):
        if service.get_conversation(conversation_id) is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        async def event_generator():
            last_seen = after
            emitted = 0
            idle_ticks = 0
            while True:
                max_batch = None if limit is None else max(limit - emitted, 0)
                if max_batch == 0:
                    break
                events = service.get_events(conversation_id, last_seen, max_batch)
                if events:
                    idle_ticks = 0
                    for event in events:
                        last_seen = max(last_seen, event["id"])
                        emitted += 1
                        yield _sse(event)
                        if limit is not None and emitted >= limit:
                            return
                else:
                    idle_ticks += 1
                    if limit is not None and idle_ticks >= 10:
                        return
                    await asyncio.sleep(0.1)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.get("/api/events/stream")
    async def stream_global_events(
        after: int = Query(0, ge=0),
        limit: Optional[int] = Query(None, ge=1),
    ):
        async def event_generator():
            last_seen = after
            emitted = 0
            idle_ticks = 0
            while True:
                max_batch = None if limit is None else max(limit - emitted, 0)
                if max_batch == 0:
                    break
                events = service.get_global_events(last_seen, max_batch)
                if events:
                    idle_ticks = 0
                    for event in events:
                        last_seen = max(last_seen, event["id"])
                        emitted += 1
                        yield _sse(event)
                        if limit is not None and emitted >= limit:
                            return
                else:
                    idle_ticks += 1
                    if limit is not None and idle_ticks >= 10:
                        return
                    await asyncio.sleep(0.1)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.post("/api/inference/jobs", status_code=202)
    async def create_inference_job(payload: dict):
        conversation_id = str(payload.get("conversation_id") or "").strip()
        user_message_id = str(payload.get("user_message_id") or "").strip()
        if not conversation_id or not user_message_id:
            raise HTTPException(
                status_code=400,
                detail="conversation_id and user_message_id are required",
            )
        job = service.create_inference_job(conversation_id, user_message_id)
        if job is None:
            raise HTTPException(
                status_code=404, detail="conversation or message not found"
            )
        if service.settings.test_mode:
            await service._run_job(job["id"])
        else:
            await service.enqueue_job(job["id"])
        return job

    @app.post("/api/inference/jobs/{job_id}/cancel")
    def cancel_inference_job(job_id: str):
        job = service.cancel_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    @app.post("/api/messages/{message_id}/ack")
    def ack_message(message_id: str, payload: dict):
        status = str(payload.get("status") or "").strip()
        ack = service.ack_message(message_id, status)
        if ack is None:
            raise HTTPException(status_code=404, detail="message not found")
        return ack

    @app.post("/api/messages/{message_id}/read")
    def read_message(message_id: str):
        ack = service.ack_message(message_id, "read")
        if ack is None:
            raise HTTPException(status_code=404, detail="message not found")
        return ack

    @app.get("/api/messages/outbox")
    def outbox():
        return service.list_outbox()

    @app.get("/api/sync/events")
    def sync_events(
        after: int = Query(0, ge=0),
        limit: Optional[int] = Query(None, ge=1),
    ):
        return service.list_sync_events(after=after, limit=limit)

    @app.get("/api/network/peers")
    def peers():
        return service.list_peers()

    @app.get("/api/network/status")
    def network_status():
        return service.network_status()

    # Backward-compatible shim used by earlier local demos/tests.
    @app.post("/api/infer")
    async def infer(payload: dict):
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt is required")
        bootstrap_data = service.bootstrap_payload()
        conversation_id = bootstrap_data["conversations"][0]["id"]
        user_message = service.create_message(conversation_id, "user", prompt)
        job = service.create_inference_job(conversation_id, user_message["id"])
        if service.settings.test_mode:
            await service._run_job(job["id"])
        else:
            await service.enqueue_job(job["id"])
        return JSONResponse(job, status_code=202)

    return app


if __name__ == "__main__":
    import uvicorn

    settings = build_settings()
    app = create_app()
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=settings.backend_port,
        reload=False,
    )
