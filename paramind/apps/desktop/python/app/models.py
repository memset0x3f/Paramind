from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid4().hex


class Conversation(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    title: str
    kind: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class Participant(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    conversation_id: str = Field(index=True)
    peer_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow)


class Message(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    conversation_id: str = Field(index=True)
    sender_id: str = Field(index=True)
    sender_name: str
    role: str = Field(index=True)
    content: str = ""
    status: str = Field(default="pending", index=True)
    metadata_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class DmRequest(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    requester_id: str = Field(index=True)
    target_peer_id: str = Field(index=True)
    status: str = Field(default="pending", index=True)
    conversation_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    responded_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class GroupInvitation(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    conversation_id: str = Field(index=True)
    inviter_id: str = Field(index=True)
    target_peer_id: str = Field(index=True)
    title: str
    participant_ids_json: str = "[]"
    status: str = Field(default="pending", index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    responded_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class PeerRelationship(SQLModel, table=True):
    peer_id: str = Field(primary_key=True)
    relationship: str = Field(default="none", index=True)
    conversation_id: Optional[str] = Field(default=None, index=True)
    request_id: Optional[str] = Field(default=None, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class InferenceJob(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    conversation_id: str = Field(index=True)
    user_message_id: str = Field(index=True)
    assistant_message_id: str = Field(index=True)
    status: str = Field(default="queued", index=True)
    model_id: str
    family: str
    route_json: str = "[]"
    error: Optional[str] = None
    prompt: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class Peer(SQLModel, table=True):
    id: str = Field(primary_key=True)
    display_name: str
    backend_port: int
    status: str = Field(default="online", index=True)
    capabilities_json: str = "{}"
    last_seen_at: datetime = Field(default_factory=utcnow, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class EventLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str = Field(index=True)
    scope: str = Field(default="conversation", index=True)
    conversation_id: Optional[str] = Field(default=None, index=True)
    entity_id: str
    payload_json: str
    created_at: datetime = Field(default_factory=utcnow, index=True)
