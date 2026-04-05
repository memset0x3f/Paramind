from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, desc, select

from .database import session_scope
from .inference import InferenceRunner
from .mock_p2p import MockP2PAdapter
from .models import (
    Conversation,
    DmRequest,
    EventLog,
    GroupInvitation,
    InferenceJob,
    Message,
    Participant,
    Peer,
    PeerRelationship,
    utcnow,
)


def _dt(value: datetime | None):
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_dt(value: str | None, fallback: datetime | None = None) -> datetime | None:
    if value:
        return datetime.fromisoformat(value)
    return fallback


class DesktopAppService:
    def __init__(self, settings, engine, p2p_transport=None):
        self.settings = settings
        self.engine = engine
        self.p2p = MockP2PAdapter(settings, transport=p2p_transport)
        self.inference = InferenceRunner(settings)
        self.job_queue: asyncio.Queue[str] = asyncio.Queue()
        self.stop_event = asyncio.Event()
        self.default_conversation_title = "General"
        self.default_conversation_id = "general"
        self.transport_event_cursor = 0

    def _upsert_relationship(
        self,
        session: Session,
        peer_id: str,
        relationship: str,
        *,
        conversation_id: str | None = None,
        request_id: str | None = None,
    ):
        if not peer_id or peer_id == self.settings.instance_id:
            return None
        row = session.get(PeerRelationship, peer_id)
        if row is None:
            row = PeerRelationship(
                peer_id=peer_id,
                relationship=relationship,
                conversation_id=conversation_id,
                request_id=request_id,
                updated_at=utcnow(),
            )
            session.add(row)
        else:
            row.relationship = relationship
            row.conversation_id = conversation_id
            row.request_id = request_id
            row.updated_at = utcnow()
        return row

    def _remove_relationship(self, session: Session, peer_id: str):
        row = session.get(PeerRelationship, peer_id)
        if row is not None:
            session.delete(row)

    def _reconcile_relationships(self, session: Session):
        peers = {
            peer.id: self._serialize_peer(peer)
            for peer in session.exec(select(Peer)).all()
            if peer.id != self.settings.instance_id
        }
        dm_requests = session.exec(
            select(DmRequest).where(
                (DmRequest.requester_id == self.settings.instance_id)
                | (DmRequest.target_peer_id == self.settings.instance_id)
            )
        ).all()
        dm_conversations = session.exec(
            select(Conversation).where(Conversation.kind == "dm")
        ).all()

        active_by_peer: dict[str, str] = {}
        for conversation in dm_conversations:
            participant_ids = self._conversation_participant_ids(
                session, conversation.id
            )
            if self.settings.instance_id in participant_ids:
                counterpart = next(
                    (
                        item
                        for item in participant_ids
                        if item != self.settings.instance_id
                    ),
                    None,
                )
                if counterpart:
                    active_by_peer[counterpart] = conversation.id

        pending_by_peer: dict[str, tuple[str, str]] = {}
        for request in dm_requests:
            counterpart = (
                request.target_peer_id
                if request.requester_id == self.settings.instance_id
                else request.requester_id
            )
            pending_by_peer[counterpart] = (
                (
                    "outbound_pending_dm"
                    if request.requester_id == self.settings.instance_id
                    else "inbound_pending_dm"
                ),
                request.id,
            )

        for peer_id in peers:
            if peer_id in active_by_peer:
                self._upsert_relationship(
                    session,
                    peer_id,
                    "active_dm",
                    conversation_id=active_by_peer[peer_id],
                )
            elif peer_id in pending_by_peer:
                relationship, request_id = pending_by_peer[peer_id]
                self._upsert_relationship(
                    session,
                    peer_id,
                    relationship,
                    request_id=request_id,
                )
            else:
                existing = session.get(PeerRelationship, peer_id)
                if existing is not None and existing.relationship == "closed_dm":
                    existing.updated_at = utcnow()
                else:
                    self._upsert_relationship(session, peer_id, "none")

    def _conversation_participant_ids(self, session: Session, conversation_id: str):
        return [
            item.peer_id
            for item in session.exec(
                select(Participant).where(
                    Participant.conversation_id == conversation_id
                )
            ).all()
        ]

    def _is_virtual_participant(self, peer_id: str | None):
        return str(peer_id or "").strip().lower() in {"assistant", "ai"}

    def _conversation_summary_participant_ids(
        self, session: Session, conversation: Conversation
    ):
        participant_ids = {
            peer_id
            for peer_id in self._conversation_participant_ids(session, conversation.id)
            if not self._is_virtual_participant(peer_id)
        }
        if (
            conversation.id == self.default_conversation_id
            and conversation.kind == "group"
        ):
            participant_ids |= {
                peer.id
                for peer in session.exec(select(Peer)).all()
                if not self._is_virtual_participant(peer.id)
            }
        return sorted(participant_ids)

    def ensure_initialized(self):
        with session_scope(self.engine) as session:
            self._seed_default_conversation(session)
            action, peer = self.p2p.upsert_self(session)
            self._record_peer_event(session, action, peer)
            self._reconcile_relationships(session)
            self._recover_jobs(session)

    def _recover_jobs(self, session: Session):
        jobs = session.exec(
            select(InferenceJob).where(
                InferenceJob.status.in_(["queued", "running", "streaming"])
            )
        ).all()
        for job in jobs:
            job.status = "failed"
            job.error = "Interrupted by app restart"
            job.finished_at = utcnow()
            assistant = session.get(Message, job.assistant_message_id)
            if assistant is not None:
                assistant.status = "failed"
                assistant.updated_at = utcnow()
            self._record_event(
                session,
                "job.failed",
                job.conversation_id,
                job.id,
                {
                    "job_id": job.id,
                    "assistant_message_id": job.assistant_message_id,
                    "error": job.error,
                },
            )

    def _seed_default_conversation(self, session: Session):
        existing = session.exec(
            select(Conversation).where(Conversation.id == self.default_conversation_id)
        ).first()
        if existing is None:
            existing = Conversation(
                id=self.default_conversation_id,
                title=self.default_conversation_title,
                kind="group",
            )
            session.add(existing)
            session.flush()
            session.add(
                Participant(
                    conversation_id=existing.id, peer_id=self.settings.instance_id
                )
            )
            self._record_event(
                session,
                "conversation.created",
                existing.id,
                existing.id,
                {"conversation": self._serialize_conversation(session, existing)},
                scope="global",
            )
        self._ensure_participant(session, existing.id, self.settings.instance_id)

    def _ensure_participant(self, session: Session, conversation_id: str, peer_id: str):
        if self._is_virtual_participant(peer_id):
            return
        existing = session.exec(
            select(Participant)
            .where(Participant.conversation_id == conversation_id)
            .where(Participant.peer_id == peer_id)
        ).first()
        if existing is None:
            session.add(Participant(conversation_id=conversation_id, peer_id=peer_id))

    def _record_peer_event(self, session: Session, action: str, peer):
        event_name = "peer.joined" if action == "joined" else "peer.updated"
        self._record_event(
            session,
            event_name,
            None,
            peer.id,
            {"peer": self._serialize_peer(peer)},
        )

    def _record_event(
        self,
        session: Session,
        event_type: str,
        conversation_id: str | None,
        entity_id: str,
        payload: dict[str, Any],
        broadcast: bool = True,
        scope: str | None = None,
    ):
        event = EventLog(
            event_type=event_type,
            scope=scope or ("global" if conversation_id is None else "conversation"),
            conversation_id=conversation_id,
            entity_id=entity_id,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        session.add(event)
        session.flush()
        if broadcast:
            try:
                self.p2p.publish_event(
                    event_type=event_type,
                    entity_id=entity_id,
                    payload=payload,
                    conversation_id=conversation_id,
                )
            except Exception:
                # Transport fanout is best-effort; local persistence remains authoritative.
                pass
        return event

    def _serialize_peer(self, peer):
        return {
            "id": peer.id,
            "display_name": peer.display_name,
            "backend_port": peer.backend_port,
            "status": peer.status,
            "capabilities": json.loads(peer.capabilities_json or "{}"),
            "last_seen_at": _dt(peer.last_seen_at),
        }

    def _serialize_message(self, message: Message):
        return {
            "id": message.id,
            "conversation_id": message.conversation_id,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "role": message.role,
            "content": message.content,
            "status": message.status,
            "metadata": json.loads(message.metadata_json or "{}"),
            "created_at": _dt(message.created_at),
            "updated_at": _dt(message.updated_at),
        }

    def _serialize_dm_request(self, request: DmRequest):
        return {
            "id": request.id,
            "requester_id": request.requester_id,
            "target_peer_id": request.target_peer_id,
            "status": request.status,
            "conversation_id": request.conversation_id,
            "created_at": _dt(request.created_at),
            "responded_at": _dt(request.responded_at),
            "updated_at": _dt(request.updated_at),
            "direction": (
                "outbound"
                if request.requester_id == self.settings.instance_id
                else (
                    "inbound"
                    if request.target_peer_id == self.settings.instance_id
                    else "external"
                )
            ),
        }

    def _serialize_group_invitation(self, invitation: GroupInvitation):
        return {
            "id": invitation.id,
            "conversation_id": invitation.conversation_id,
            "inviter_id": invitation.inviter_id,
            "target_peer_id": invitation.target_peer_id,
            "title": invitation.title,
            "participant_ids": json.loads(invitation.participant_ids_json or "[]"),
            "status": invitation.status,
            "created_at": _dt(invitation.created_at),
            "responded_at": _dt(invitation.responded_at),
            "updated_at": _dt(invitation.updated_at),
            "direction": (
                "outbound"
                if invitation.inviter_id == self.settings.instance_id
                else (
                    "inbound"
                    if invitation.target_peer_id == self.settings.instance_id
                    else "external"
                )
            ),
        }

    def _serialize_relationship(self, relationship: PeerRelationship):
        return {
            "peer_id": relationship.peer_id,
            "relationship": relationship.relationship,
            "conversation_id": relationship.conversation_id,
            "request_id": relationship.request_id,
            "updated_at": _dt(relationship.updated_at),
        }

    def _serialize_conversation(self, session: Session, conversation: Conversation):
        last_message = session.exec(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .where(~Message.metadata_json.contains('"local_draft": true'))
            .order_by(desc(Message.created_at))
        ).first()
        participants = session.exec(
            select(Participant).where(Participant.conversation_id == conversation.id)
        ).all()
        return {
            "id": conversation.id,
            "title": conversation.title,
            "kind": conversation.kind,
            "updated_at": _dt(conversation.updated_at),
            "participant_ids": self._conversation_summary_participant_ids(
                session, conversation
            ),
            "last_message": (
                self._serialize_message(last_message) if last_message else None
            ),
        }

    def _serialize_conversation_messages(self, session: Session, conversation_id: str):
        rows = session.exec(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(~Message.metadata_json.contains('"local_draft": true'))
            .order_by(Message.created_at, Message.id)
        ).all()
        return [self._serialize_message(item) for item in rows]

    def _create_system_message(
        self,
        session: Session,
        conversation: Conversation,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        broadcast: bool = True,
    ):
        metadata_dict = {
            "conversation_title": conversation.title,
            "conversation_kind": conversation.kind,
            "participant_ids": self._conversation_summary_participant_ids(
                session, conversation
            ),
            "local_draft": False,
        }
        if metadata:
            metadata_dict.update(metadata)
        message = Message(
            conversation_id=conversation.id,
            sender_id="system",
            sender_name="System",
            role="system",
            content=content,
            status="sent",
            metadata_json=json.dumps(metadata_dict, ensure_ascii=False),
        )
        session.add(message)
        session.flush()
        conversation.updated_at = message.updated_at
        self._record_event(
            session,
            "message.created",
            conversation.id,
            message.id,
            {"message": self._serialize_message(message)},
            broadcast=broadcast,
        )
        self._record_event(
            session,
            "conversation.updated",
            conversation.id,
            conversation.id,
            {"conversation": self._serialize_conversation(session, conversation)},
            broadcast=broadcast,
            scope="global",
        )
        return message

    def _latest_conversation_event_id(self, session: Session, conversation_id: str):
        return (
            session.exec(
                select(EventLog.id)
                .where(EventLog.scope == "conversation")
                .where(EventLog.conversation_id == conversation_id)
                .order_by(desc(EventLog.id))
                .limit(1)
            ).first()
            or 0
        )

    def _backfill_conversation_history_from_transport(
        self, session: Session, conversation_id: str
    ):
        for event in self.p2p.list_events(after=0, limit=None):
            if str(event.get("conversation_id") or "") != conversation_id:
                continue
            event_type = str(event.get("type") or "")
            if event_type in {"message.created", "message.updated"}:
                self._apply_remote_message_event(session, event)
            elif event_type in {"message.ack", "message.read"}:
                self._apply_remote_message_ack_event(session, event)

    def bootstrap_payload(self):
        self.process_transport_events()
        with session_scope(self.engine) as session:
            self.p2p.sweep_stale_peers(session)
            self.p2p.upsert_self(session)
            self._reconcile_relationships(session)
            latest_global_event_id = (
                session.exec(
                    select(EventLog.id)
                    .where(EventLog.scope == "global")
                    .order_by(desc(EventLog.id))
                    .limit(1)
                ).first()
                or 0
            )
            latest_conversation_events: dict[str, int] = {}
            for row in session.exec(
                select(EventLog)
                .where(EventLog.scope == "conversation")
                .order_by(EventLog.id)
            ).all():
                if row.conversation_id:
                    latest_conversation_events[row.conversation_id] = int(row.id or 0)
            conversations = session.exec(
                select(Conversation).order_by(desc(Conversation.updated_at))
            ).all()
            peers = self.p2p.list_peers(session)
            dm_requests = session.exec(
                select(DmRequest)
                .where(
                    (DmRequest.requester_id == self.settings.instance_id)
                    | (DmRequest.target_peer_id == self.settings.instance_id)
                )
                .where(DmRequest.status == "pending")
                .order_by(desc(DmRequest.updated_at))
            ).all()
            group_invitations = session.exec(
                select(GroupInvitation)
                .where(
                    (GroupInvitation.inviter_id == self.settings.instance_id)
                    | (GroupInvitation.target_peer_id == self.settings.instance_id)
                )
                .where(GroupInvitation.status == "pending")
                .order_by(desc(GroupInvitation.updated_at))
            ).all()
            relationships = session.exec(
                select(PeerRelationship).order_by(PeerRelationship.peer_id)
            ).all()
            return {
                "self": {
                    "id": self.settings.instance_id,
                    "display_name": self.settings.instance_name,
                    "backend_port": self.settings.backend_port,
                },
                "model": {
                    "model_id": self.settings.model_id,
                    "family": self.settings.family,
                    "device": self.settings.device,
                },
                "network": {
                    "transport": self.settings.transport,
                    "peer_count": len(
                        [peer for peer in peers if peer.status == "online"]
                    ),
                },
                "latest_event_id": int(latest_global_event_id),
                "latest_global_event_id": int(latest_global_event_id),
                "latest_conversation_event_ids": latest_conversation_events,
                "conversations": [
                    self._serialize_conversation(session, conversation)
                    for conversation in conversations
                ],
                "peers": [self._serialize_peer(peer) for peer in peers],
                "dm_requests": [
                    self._serialize_dm_request(item) for item in dm_requests
                ],
                "group_invitations": [
                    self._serialize_group_invitation(item) for item in group_invitations
                ],
                "relationships": [
                    self._serialize_relationship(item) for item in relationships
                ],
            }

    def list_conversations(self):
        self.process_transport_events()
        with session_scope(self.engine) as session:
            conversations = session.exec(
                select(Conversation).order_by(desc(Conversation.updated_at))
            ).all()
            return [
                self._serialize_conversation(session, conversation)
                for conversation in conversations
            ]

    def create_conversation(
        self, title: str, kind: str, participant_ids: list[str] | None = None
    ):
        participant_ids = participant_ids or []
        with session_scope(self.engine) as session:
            unique_participants = sorted({self.settings.instance_id, *participant_ids})
            conversation_id = None
            if kind == "dm" and unique_participants:
                conversation_id = f"dm:{'-'.join(unique_participants)}"
            conversation = (
                session.get(Conversation, conversation_id) if conversation_id else None
            )
            created = conversation is None
            if conversation is None:
                conversation = Conversation(
                    id=conversation_id or None, title=title, kind=kind
                )
                session.add(conversation)
                session.flush()
            else:
                conversation.title = title or conversation.title
                conversation.kind = kind or conversation.kind

            for peer_id in unique_participants:
                self._ensure_participant(session, conversation.id, peer_id)

            conversation.updated_at = utcnow()
            payload = self._serialize_conversation(session, conversation)
            self._record_event(
                session,
                "conversation.created" if created else "conversation.updated",
                conversation.id,
                conversation.id,
                {"conversation": payload},
                scope="global",
            )
            return payload

    def list_dm_requests(self):
        self.process_transport_events()
        with session_scope(self.engine) as session:
            self._reconcile_relationships(session)
            rows = session.exec(
                select(DmRequest)
                .where(
                    (DmRequest.requester_id == self.settings.instance_id)
                    | (DmRequest.target_peer_id == self.settings.instance_id)
                )
                .where(DmRequest.status == "pending")
                .order_by(desc(DmRequest.updated_at))
            ).all()
            return [self._serialize_dm_request(item) for item in rows]

    def list_group_invitations(self):
        self.process_transport_events()
        with session_scope(self.engine) as session:
            rows = session.exec(
                select(GroupInvitation)
                .where(
                    (GroupInvitation.inviter_id == self.settings.instance_id)
                    | (GroupInvitation.target_peer_id == self.settings.instance_id)
                )
                .where(GroupInvitation.status == "pending")
                .order_by(desc(GroupInvitation.updated_at))
            ).all()
            return [self._serialize_group_invitation(item) for item in rows]

    def create_dm_request(self, target_peer_id: str):
        with session_scope(self.engine) as session:
            existing_dm = session.get(
                Conversation,
                f"dm:{'-'.join(sorted([self.settings.instance_id, target_peer_id]))}",
            )
            if existing_dm is not None:
                return {
                    "request": None,
                    "conversation": self._serialize_conversation(session, existing_dm),
                }

            existing_request = session.exec(
                select(DmRequest)
                .where(
                    (
                        (DmRequest.requester_id == self.settings.instance_id)
                        & (DmRequest.target_peer_id == target_peer_id)
                    )
                    | (
                        (DmRequest.requester_id == target_peer_id)
                        & (DmRequest.target_peer_id == self.settings.instance_id)
                    )
                )
                .where(DmRequest.status == "pending")
            ).first()
            if existing_request is not None:
                return self._serialize_dm_request(existing_request)

            request = DmRequest(
                requester_id=self.settings.instance_id,
                target_peer_id=target_peer_id,
                status="pending",
            )
            session.add(request)
            session.flush()
            self._upsert_relationship(
                session,
                target_peer_id,
                "outbound_pending_dm",
                request_id=request.id,
            )
            payload = self._serialize_dm_request(request)
            self._record_event(
                session,
                "dm.requested",
                None,
                request.id,
                {
                    "request": payload,
                    "relationship": {
                        "peer_id": target_peer_id,
                        "relationship": "outbound_pending_dm",
                        "request_id": request.id,
                    },
                },
            )
            return payload

    def create_group_invitations(
        self,
        title: str,
        target_peer_ids: list[str],
        conversation_id: str | None = None,
    ):
        with session_scope(self.engine) as session:
            requested_target_ids = sorted(
                {
                    str(item)
                    for item in target_peer_ids
                    if str(item).strip() and str(item) != self.settings.instance_id
                }
            )
            if not requested_target_ids:
                return None

            conversation = (
                session.get(Conversation, conversation_id) if conversation_id else None
            )
            created_conversation = False

            if conversation_id:
                if conversation is None or conversation.kind != "group":
                    return None
                existing_participants = set(
                    self._conversation_participant_ids(session, conversation.id)
                )
                if self.settings.instance_id not in existing_participants:
                    return None
                if conversation.id == self.default_conversation_id:
                    return None
                title = conversation.title
            else:
                unique_participants = sorted(
                    {self.settings.instance_id, *requested_target_ids}
                )
                if len(unique_participants) < 2:
                    return None
                conversation = Conversation(title=title, kind="group")
                session.add(conversation)
                session.flush()
                self._ensure_participant(
                    session, conversation.id, self.settings.instance_id
                )
                created_conversation = True

            pending_targets = {
                row.target_peer_id
                for row in session.exec(
                    select(GroupInvitation)
                    .where(GroupInvitation.conversation_id == conversation.id)
                    .where(GroupInvitation.status == "pending")
                ).all()
            }
            existing_participants = set(
                self._conversation_participant_ids(session, conversation.id)
            )
            invite_targets = [
                peer_id
                for peer_id in requested_target_ids
                if peer_id not in existing_participants
                and peer_id not in pending_targets
            ]
            if not invite_targets:
                return None

            summary_participants = sorted(existing_participants)
            conversation_payload = self._serialize_conversation(session, conversation)
            invitations = []
            for peer_id in invite_targets:
                invitation = GroupInvitation(
                    conversation_id=conversation.id,
                    inviter_id=self.settings.instance_id,
                    target_peer_id=peer_id,
                    title=title,
                    participant_ids_json=json.dumps(
                        summary_participants, ensure_ascii=False
                    ),
                    status="pending",
                )
                session.add(invitation)
                session.flush()
                invitations.append(self._serialize_group_invitation(invitation))
                self._record_event(
                    session,
                    "group.invited",
                    None,
                    invitation.id,
                    {
                        "invitation": invitations[-1],
                        "conversation": conversation_payload,
                    },
                    scope="global",
                )
            if created_conversation:
                self._record_event(
                    session,
                    "conversation.created",
                    conversation.id,
                    conversation.id,
                    {"conversation": conversation_payload},
                    broadcast=False,
                    scope="global",
                )
            return {"conversation": conversation_payload, "invitations": invitations}

    def _ensure_dm_conversation(
        self,
        session: Session,
        peer_a: str,
        peer_b: str,
        title: str | None = None,
    ):
        participant_ids = sorted({peer_a, peer_b})
        conversation_id = f"dm:{'-'.join(participant_ids)}"
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            conversation = Conversation(
                id=conversation_id,
                title=title or peer_b,
                kind="dm",
            )
            session.add(conversation)
            session.flush()
        for peer_id in participant_ids:
            self._ensure_participant(session, conversation.id, peer_id)
        conversation.updated_at = utcnow()
        return conversation

    def respond_dm_request(self, request_id: str, accept: bool):
        with session_scope(self.engine) as session:
            request = session.get(DmRequest, request_id)
            if request is None:
                return None
            if request.target_peer_id != self.settings.instance_id:
                return None
            if request.status != "pending":
                return None

            request.status = "accepted" if accept else "rejected"
            request.responded_at = utcnow()
            request.updated_at = utcnow()
            conversation_payload = None
            if accept:
                conversation = self._ensure_dm_conversation(
                    session,
                    request.requester_id,
                    request.target_peer_id,
                    title=self.settings.instance_name,
                )
                request.conversation_id = conversation.id
                conversation_payload = self._serialize_conversation(
                    session, conversation
                )
                self._record_event(
                    session,
                    "conversation.created",
                    conversation.id,
                    conversation.id,
                    {"conversation": conversation_payload},
                    scope="global",
                )
                self._upsert_relationship(
                    session,
                    request.requester_id,
                    "active_dm",
                    conversation_id=conversation.id,
                )
            else:
                self._upsert_relationship(session, request.requester_id, "none")

            payload = self._serialize_dm_request(request)
            event_type = "dm.accepted" if accept else "dm.rejected"
            self._record_event(
                session,
                event_type,
                None,
                request.id,
                {
                    "request": payload,
                    "conversation": conversation_payload,
                    "relationship": (
                        self._serialize_relationship(
                            session.get(PeerRelationship, request.requester_id)
                        )
                        if session.get(PeerRelationship, request.requester_id)
                        is not None
                        else None
                    ),
                },
            )
            session.delete(request)
            return payload

    def respond_group_invitation(self, invitation_id: str, accept: bool):
        self.process_transport_events()
        with session_scope(self.engine) as session:
            invitation = session.get(GroupInvitation, invitation_id)
            if (
                invitation is None
                or invitation.target_peer_id != self.settings.instance_id
            ):
                return None
            if invitation.status != "pending":
                return None

            invitation.status = "accepted" if accept else "rejected"
            invitation.responded_at = utcnow()
            invitation.updated_at = utcnow()
            participant_ids = [
                str(item)
                for item in json.loads(invitation.participant_ids_json or "[]")
            ]
            if self.settings.instance_id not in participant_ids:
                participant_ids.append(self.settings.instance_id)
            participant_ids = sorted(set(participant_ids))

            conversation = session.get(Conversation, invitation.conversation_id)
            conversation_payload = None
            messages_payload = []
            latest_conversation_event_id = 0
            if accept:
                if conversation is None:
                    conversation = Conversation(
                        id=invitation.conversation_id,
                        title=invitation.title,
                        kind="group",
                    )
                    session.add(conversation)
                    session.flush()
                existing_participants = set(
                    self._conversation_participant_ids(session, conversation.id)
                )
                for peer_id in sorted(
                    existing_participants
                    | set(participant_ids)
                    | {invitation.inviter_id, self.settings.instance_id}
                ):
                    self._ensure_participant(session, conversation.id, peer_id)
                conversation.updated_at = utcnow()
                self._backfill_conversation_history_from_transport(
                    session, conversation.id
                )
                self._create_system_message(
                    session,
                    conversation,
                    f"{self.settings.instance_name} joined the group",
                    metadata={
                        "kind": "group.member_joined",
                        "peer_id": self.settings.instance_id,
                    },
                    broadcast=True,
                )
                conversation_payload = self._serialize_conversation(
                    session, conversation
                )
                messages_payload = self._serialize_conversation_messages(
                    session, conversation.id
                )
                latest_conversation_event_id = int(
                    self._latest_conversation_event_id(session, conversation.id)
                )
                self._record_event(
                    session,
                    "conversation.created",
                    conversation.id,
                    conversation.id,
                    {"conversation": conversation_payload},
                    scope="global",
                )

            payload = self._serialize_group_invitation(invitation)
            event_type = "group.accepted" if accept else "group.rejected"
            self._record_event(
                session,
                event_type,
                None,
                invitation.id,
                {
                    "invitation": payload,
                    "conversation": conversation_payload,
                    "messages": messages_payload,
                    "latest_conversation_event_id": latest_conversation_event_id,
                },
                scope="global",
            )
            session.delete(invitation)
            return (
                {
                    "invitation": payload,
                    "conversation": conversation_payload,
                    "messages": messages_payload,
                    "latest_conversation_event_id": latest_conversation_event_id,
                }
                if accept
                else payload
            )

    def _ensure_remote_conversation(
        self, session: Session, conversation_payload: dict[str, Any]
    ):
        conversation_id = str(conversation_payload["id"])
        conversation = session.get(Conversation, conversation_id)
        created = conversation is None
        remote_updated_at = (
            _parse_dt(conversation_payload.get("updated_at"), utcnow()) or utcnow()
        )
        if conversation is None:
            conversation = Conversation(
                id=conversation_id,
                title=str(conversation_payload.get("title") or conversation_id),
                kind=str(conversation_payload.get("kind") or "group"),
                updated_at=remote_updated_at,
            )
            session.add(conversation)
            session.flush()
        else:
            conversation.title = str(
                conversation_payload.get("title") or conversation.title
            )
            conversation.kind = str(
                conversation_payload.get("kind") or conversation.kind
            )
            conversation.updated_at = remote_updated_at
        next_participant_ids = {
            str(item) for item in conversation_payload.get("participant_ids") or []
        }
        for peer_id in next_participant_ids:
            self._ensure_participant(session, conversation.id, str(peer_id))
        existing_participants = session.exec(
            select(Participant).where(Participant.conversation_id == conversation.id)
        ).all()
        for participant in existing_participants:
            if participant.peer_id not in next_participant_ids:
                session.delete(participant)
        return conversation, created

    def _apply_remote_peer_event(self, session: Session, event: dict[str, Any]):
        peer_payload = dict(event.get("payload", {}).get("peer") or {})
        if not peer_payload:
            return
        peer = session.get(Peer, peer_payload["id"])
        if peer is None:
            peer = Peer(
                id=str(peer_payload["id"]),
                display_name=str(
                    peer_payload.get("display_name") or peer_payload["id"]
                ),
                backend_port=int(peer_payload.get("backend_port") or 0),
                status=str(peer_payload.get("status") or "online"),
                capabilities_json=json.dumps(peer_payload.get("capabilities") or {}),
                last_seen_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(peer)
        else:
            peer.display_name = str(
                peer_payload.get("display_name") or peer.display_name
            )
            peer.backend_port = int(
                peer_payload.get("backend_port") or peer.backend_port
            )
            peer.status = str(peer_payload.get("status") or peer.status)
            peer.capabilities_json = json.dumps(peer_payload.get("capabilities") or {})
            peer.last_seen_at = utcnow()
            peer.updated_at = utcnow()
        self._record_event(
            session,
            event["type"],
            None,
            peer.id,
            {"peer": self._serialize_peer(peer)},
            broadcast=False,
        )

    def _apply_remote_conversation_event(self, session: Session, event: dict[str, Any]):
        payload = dict(event.get("payload", {}).get("conversation") or {})
        if not payload:
            return
        participant_ids = {str(item) for item in payload.get("participant_ids") or []}
        if participant_ids and self.settings.instance_id not in participant_ids:
            existing = session.get(Conversation, str(payload["id"]))
            if existing is not None:
                self._apply_remote_conversation_deleted_event(
                    session,
                    {
                        "payload": {
                            "conversation_id": existing.id,
                            "kind": existing.kind,
                        },
                        "conversation_id": existing.id,
                        "entity_id": existing.id,
                    },
                )
            return
        conversation, created = self._ensure_remote_conversation(session, payload)
        self._record_event(
            session,
            "conversation.created" if created else "conversation.updated",
            conversation.id,
            conversation.id,
            {"conversation": self._serialize_conversation(session, conversation)},
            broadcast=False,
            scope="global",
        )

    def _apply_remote_conversation_deleted_event(
        self, session: Session, event: dict[str, Any]
    ):
        payload = dict(event.get("payload") or {})
        conversation_id = str(
            payload.get("conversation_id")
            or event.get("conversation_id")
            or event.get("entity_id")
            or ""
        )
        if not conversation_id:
            return
        conversation = session.get(Conversation, conversation_id)
        counterpart = None
        conversation_kind = str(
            payload.get("kind") or (conversation.kind if conversation else "")
        )
        if conversation is not None and conversation.kind == "dm":
            participant_ids = self._conversation_participant_ids(
                session, conversation_id
            )
            counterpart = next(
                (item for item in participant_ids if item != self.settings.instance_id),
                None,
            )
        if conversation is not None:
            messages = session.exec(
                select(Message).where(Message.conversation_id == conversation_id)
            ).all()
            for message in messages:
                session.delete(message)
            participants = session.exec(
                select(Participant).where(
                    Participant.conversation_id == conversation_id
                )
            ).all()
            for participant in participants:
                session.delete(participant)
            session.delete(conversation)
        if conversation_kind == "dm" and counterpart:
            self._upsert_relationship(session, counterpart, "closed_dm")
        self._record_event(
            session,
            "conversation.deleted",
            None,
            conversation_id,
            {"conversation_id": conversation_id, "kind": conversation_kind or None},
            broadcast=False,
            scope="global",
        )

    def _apply_remote_message_event(self, session: Session, event: dict[str, Any]):
        payload = dict(event.get("payload", {}).get("message") or {})
        if not payload:
            return
        existing_conversation = session.get(
            Conversation, str(payload["conversation_id"])
        )
        participant_ids = {
            str(item)
            for item in payload.get("metadata", {}).get("participant_ids") or []
        }
        if (
            existing_conversation is None
            and participant_ids
            and self.settings.instance_id not in participant_ids
            and str(payload["conversation_id"]) != self.default_conversation_id
        ):
            return
        if existing_conversation is not None and existing_conversation.kind == "group":
            participant_ids |= set(
                self._conversation_participant_ids(session, existing_conversation.id)
            )
        conversation_payload = {
            "id": payload["conversation_id"],
            "title": payload.get("metadata", {}).get("conversation_title")
            or self.default_conversation_title,
            "kind": payload.get("metadata", {}).get("conversation_kind") or "group",
            "participant_ids": sorted(participant_ids),
        }
        conversation, _ = self._ensure_remote_conversation(
            session, conversation_payload
        )
        message = session.get(Message, payload["id"])
        remote_created_at = _parse_dt(payload.get("created_at"), utcnow()) or utcnow()
        remote_updated_at = (
            _parse_dt(payload.get("updated_at"), remote_created_at) or remote_created_at
        )
        if message is None:
            message = Message(
                id=str(payload["id"]),
                conversation_id=conversation.id,
                sender_id=str(payload.get("sender_id") or "peer"),
                sender_name=str(payload.get("sender_name") or "Peer"),
                role=(
                    "peer"
                    if payload.get("role") == "user"
                    else str(payload.get("role") or "peer")
                ),
                content=str(payload.get("content") or ""),
                status=str(payload.get("status") or "sent"),
                metadata_json=json.dumps(payload.get("metadata") or {}),
                created_at=remote_created_at,
                updated_at=remote_updated_at,
            )
            session.add(message)
            session.flush()
        else:
            message.sender_name = str(payload.get("sender_name") or message.sender_name)
            message.role = (
                "peer"
                if payload.get("role") == "user"
                else str(payload.get("role") or message.role)
            )
            message.content = str(payload.get("content") or message.content)
            message.status = str(payload.get("status") or message.status)
            message.metadata_json = json.dumps(payload.get("metadata") or {})
            message.created_at = remote_created_at
            message.updated_at = remote_updated_at

        conversation.updated_at = remote_updated_at
        self._record_event(
            session,
            "conversation.updated",
            conversation.id,
            conversation.id,
            {"conversation": self._serialize_conversation(session, conversation)},
            broadcast=False,
            scope="global",
        )
        self._record_event(
            session,
            event["type"],
            conversation.id,
            message.id,
            {"message": self._serialize_message(message)},
            broadcast=False,
        )

    def _apply_remote_dm_request_event(self, session: Session, event: dict[str, Any]):
        payload = dict(event.get("payload", {}).get("request") or {})
        if not payload:
            return
        request_id = str(payload["id"])
        request = session.get(DmRequest, request_id)

        conversation_payload = dict(event.get("payload", {}).get("conversation") or {})
        if conversation_payload:
            conversation, created = self._ensure_remote_conversation(
                session, conversation_payload
            )
            self._record_event(
                session,
                "conversation.created" if created else "conversation.updated",
                conversation.id,
                conversation.id,
                {"conversation": self._serialize_conversation(session, conversation)},
                broadcast=False,
                scope="global",
            )

        if event["type"] == "dm.requested":
            if request is None:
                request = DmRequest(
                    id=request_id,
                    requester_id=str(payload["requester_id"]),
                    target_peer_id=str(payload["target_peer_id"]),
                    status=str(payload.get("status") or "pending"),
                    conversation_id=payload.get("conversation_id"),
                    created_at=(
                        datetime.fromisoformat(payload["created_at"])
                        if payload.get("created_at")
                        else utcnow()
                    ),
                    responded_at=(
                        datetime.fromisoformat(payload["responded_at"])
                        if payload.get("responded_at")
                        else None
                    ),
                    updated_at=(
                        datetime.fromisoformat(payload["updated_at"])
                        if payload.get("updated_at")
                        else utcnow()
                    ),
                )
                session.add(request)
            else:
                request.status = str(payload.get("status") or request.status)
                request.conversation_id = payload.get("conversation_id")
                request.responded_at = (
                    datetime.fromisoformat(payload["responded_at"])
                    if payload.get("responded_at")
                    else request.responded_at
                )
                request.updated_at = (
                    datetime.fromisoformat(payload["updated_at"])
                    if payload.get("updated_at")
                    else utcnow()
                )
            counterpart = (
                request.target_peer_id
                if request.requester_id == self.settings.instance_id
                else request.requester_id
            )
            relationship = (
                "outbound_pending_dm"
                if request.requester_id == self.settings.instance_id
                else "inbound_pending_dm"
            )
            self._upsert_relationship(
                session, counterpart, relationship, request_id=request.id
            )
            self._record_event(
                session,
                event["type"],
                None,
                request.id,
                {"request": self._serialize_dm_request(request)},
                broadcast=False,
                scope="global",
            )
            return

        if request is not None:
            counterpart = (
                request.target_peer_id
                if request.requester_id == self.settings.instance_id
                else request.requester_id
            )
            if event["type"] == "dm.accepted" and conversation_payload:
                self._upsert_relationship(
                    session,
                    counterpart,
                    "active_dm",
                    conversation_id=str(
                        conversation_payload.get("id") or request.conversation_id or ""
                    ),
                )
            else:
                self._upsert_relationship(session, counterpart, "none")
            session.delete(request)
        self._record_event(
            session,
            event["type"],
            None,
            request_id,
            {"request": payload, "conversation": conversation_payload or None},
            broadcast=False,
            scope="global",
        )

    def _apply_remote_group_invitation_event(
        self, session: Session, event: dict[str, Any]
    ):
        payload = dict(event.get("payload", {}).get("invitation") or {})
        if not payload:
            return
        invitation_id = str(payload["id"])
        direct_party = self.settings.instance_id in {
            str(payload.get("inviter_id") or ""),
            str(payload.get("target_peer_id") or ""),
        }
        invitation = session.get(GroupInvitation, invitation_id)
        conversation_payload = dict(event.get("payload", {}).get("conversation") or {})
        if event["type"] == "group.invited":
            if not direct_party:
                return
            if invitation is None:
                invitation = GroupInvitation(
                    id=invitation_id,
                    conversation_id=str(payload["conversation_id"]),
                    inviter_id=str(payload["inviter_id"]),
                    target_peer_id=str(payload["target_peer_id"]),
                    title=str(payload["title"]),
                    participant_ids_json=json.dumps(
                        payload.get("participant_ids") or [], ensure_ascii=False
                    ),
                    status=str(payload.get("status") or "pending"),
                    created_at=(
                        datetime.fromisoformat(payload["created_at"])
                        if payload.get("created_at")
                        else utcnow()
                    ),
                    responded_at=(
                        datetime.fromisoformat(payload["responded_at"])
                        if payload.get("responded_at")
                        else None
                    ),
                    updated_at=(
                        datetime.fromisoformat(payload["updated_at"])
                        if payload.get("updated_at")
                        else utcnow()
                    ),
                )
                session.add(invitation)
            else:
                invitation.status = str(payload.get("status") or invitation.status)
                invitation.updated_at = (
                    datetime.fromisoformat(payload["updated_at"])
                    if payload.get("updated_at")
                    else utcnow()
                )
            self._record_event(
                session,
                "group.invited",
                None,
                invitation.id,
                {
                    "invitation": self._serialize_group_invitation(invitation),
                    "conversation": conversation_payload or None,
                },
                broadcast=False,
                scope="global",
            )
            return

        if invitation is not None:
            session.delete(invitation)
        if conversation_payload and event["type"] == "group.accepted":
            existing = session.get(Conversation, str(conversation_payload["id"]))
            merged_payload = dict(conversation_payload)
            if existing is not None:
                merged_payload["participant_ids"] = sorted(
                    set(self._conversation_participant_ids(session, existing.id))
                    | {
                        str(item)
                        for item in conversation_payload.get("participant_ids") or []
                    }
                )
            merged_participants = {
                str(item) for item in merged_payload.get("participant_ids") or []
            }
            if (
                merged_participants
                and self.settings.instance_id not in merged_participants
            ):
                if existing is not None:
                    self._apply_remote_conversation_deleted_event(
                        session,
                        {
                            "payload": {
                                "conversation_id": existing.id,
                                "kind": existing.kind,
                            },
                            "conversation_id": existing.id,
                            "entity_id": existing.id,
                        },
                    )
                self._record_event(
                    session,
                    event["type"],
                    None,
                    invitation_id,
                    {
                        "invitation": payload,
                        "conversation": conversation_payload or None,
                    },
                    broadcast=False,
                    scope="global",
                )
                return
            conversation, created = self._ensure_remote_conversation(
                session, merged_payload
            )
            self._record_event(
                session,
                "conversation.created" if created else "conversation.updated",
                conversation.id,
                conversation.id,
                {"conversation": self._serialize_conversation(session, conversation)},
                broadcast=False,
                scope="global",
            )
        if direct_party:
            self._record_event(
                session,
                event["type"],
                None,
                invitation_id,
                {"invitation": payload, "conversation": conversation_payload or None},
                broadcast=False,
                scope="global",
            )

    def _apply_remote_message_ack_event(self, session: Session, event: dict[str, Any]):
        payload = dict(event.get("payload") or {})
        message_id = str(payload.get("message_id") or event.get("entity_id") or "")
        if not message_id:
            return
        message = session.get(Message, message_id)
        if message is None:
            return
        ack_status = str(payload.get("status") or "sent")
        status_order = {
            "draft": 0,
            "queued": 1,
            "sent": 2,
            "delivered": 3,
            "read": 4,
            "failed": -1,
        }
        if status_order.get(ack_status, 0) >= status_order.get(message.status, 0):
            message.status = ack_status
            message.updated_at = utcnow()
        metadata = json.loads(message.metadata_json or "{}")
        ack_by = str(payload.get("by_peer_id") or "unknown")
        metadata.setdefault("acks", {})[ack_by] = ack_status
        message.metadata_json = json.dumps(metadata, ensure_ascii=False)
        self._record_event(
            session,
            "message.updated",
            message.conversation_id,
            message.id,
            {"message": self._serialize_message(message)},
            broadcast=False,
        )
        self._record_event(
            session,
            "message.ack",
            message.conversation_id,
            message.id,
            payload,
            broadcast=False,
        )
        if ack_status == "read":
            self._record_event(
                session,
                "message.read",
                message.conversation_id,
                message.id,
                payload,
                broadcast=False,
            )

    def process_transport_events(self):
        events = self.p2p.list_events(after=self.transport_event_cursor, limit=200)
        if not events:
            return
        with session_scope(self.engine) as session:
            for event in events:
                self.transport_event_cursor = max(
                    self.transport_event_cursor, int(event["id"])
                )
                if str(event.get("entity_id")) == self.settings.instance_id:
                    continue
                event_type = str(event.get("type") or "")
                if event_type.startswith("peer."):
                    self._apply_remote_peer_event(session, event)
                elif event_type == "conversation.updated":
                    self._apply_remote_conversation_event(session, event)
                elif event_type == "conversation.deleted":
                    self._apply_remote_conversation_deleted_event(session, event)
                elif event_type.startswith("dm."):
                    self._apply_remote_dm_request_event(session, event)
                elif event_type.startswith("group."):
                    self._apply_remote_group_invitation_event(session, event)
                elif (
                    event_type.startswith("message.") and event_type != "message.token"
                ):
                    payload_message = dict(
                        event.get("payload", {}).get("message") or {}
                    )
                    if (
                        payload_message
                        and str(payload_message.get("sender_id") or "")
                        == self.settings.instance_id
                    ):
                        continue
                    if event_type in {"message.ack", "message.read"}:
                        self._apply_remote_message_ack_event(session, event)
                    elif event_type == "message.deleted":
                        self._record_event(
                            session,
                            event_type,
                            event.get("conversation_id"),
                            str(event.get("entity_id") or "remote"),
                            dict(event.get("payload") or {}),
                            broadcast=False,
                        )
                    else:
                        self._apply_remote_message_event(session, event)
                elif event_type in {
                    "job.route",
                    "job.started",
                    "job.completed",
                    "job.failed",
                    "route.planned",
                }:
                    self._record_event(
                        session,
                        event_type,
                        event.get("conversation_id"),
                        str(event.get("entity_id") or "remote"),
                        dict(event.get("payload") or {}),
                        broadcast=False,
                    )

    def get_conversation(self, conversation_id: str):
        self.process_transport_events()
        with session_scope(self.engine) as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return None
            return self._serialize_conversation(session, conversation)

    def list_messages(self, conversation_id: str):
        self.process_transport_events()
        with session_scope(self.engine) as session:
            messages = session.exec(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at)
            ).all()
            return [self._serialize_message(message) for message in messages]

    def create_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        local_draft: bool = False,
        metadata: dict[str, Any] | None = None,
    ):
        with session_scope(self.engine) as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return None

            sender_id = (
                self.settings.instance_id if role != "assistant" else "assistant"
            )
            sender_name = (
                self.settings.instance_name if role in {"user", "peer"} else "AI"
            )
            metadata_dict = {
                "conversation_title": conversation.title,
                "conversation_kind": conversation.kind,
                "participant_ids": self._conversation_summary_participant_ids(
                    session, conversation
                ),
                "local_draft": local_draft,
            }
            if metadata:
                metadata_dict.update(metadata)
            status = (
                "queued"
                if role == "user"
                else (
                    "pending"
                    if role == "assistant" and local_draft
                    else ("completed" if role == "assistant" else "sent")
                )
            )
            message = Message(
                conversation_id=conversation_id,
                sender_id=sender_id,
                sender_name=sender_name,
                role=role,
                content=content,
                status=status,
                metadata_json=json.dumps(metadata_dict, ensure_ascii=False),
            )
            session.add(message)
            session.flush()
            self._ensure_participant(session, conversation_id, sender_id)
            if role == "user":
                message.status = "sent"
                message.updated_at = utcnow()

            if not local_draft:
                conversation.updated_at = utcnow()
            self._record_event(
                session,
                "message.created",
                conversation_id,
                message.id,
                {"message": self._serialize_message(message)},
                broadcast=not local_draft,
            )
            if not local_draft:
                self._record_event(
                    session,
                    "conversation.updated",
                    conversation_id,
                    conversation_id,
                    {
                        "conversation": self._serialize_conversation(
                            session, conversation
                        )
                    },
                    scope="global",
                )
            return self._serialize_message(message)

    def update_ai_draft(self, draft_id: str, content: str):
        with session_scope(self.engine) as session:
            draft = session.get(Message, draft_id)
            if draft is None:
                return None
            metadata = json.loads(draft.metadata_json or "{}")
            if metadata.get("local_draft") is not True:
                return None
            draft.content = content
            draft.updated_at = utcnow()
            self._record_event(
                session,
                "message.updated",
                draft.conversation_id,
                draft.id,
                {"message": self._serialize_message(draft)},
                broadcast=False,
            )
            return self._serialize_message(draft)

    def delete_ai_draft(self, draft_id: str):
        with session_scope(self.engine) as session:
            draft = session.get(Message, draft_id)
            if draft is None:
                return None
            metadata = json.loads(draft.metadata_json or "{}")
            if metadata.get("local_draft") is not True:
                return None
            payload = {
                "message_id": draft.id,
                "conversation_id": draft.conversation_id,
            }
            conversation_id = draft.conversation_id
            session.delete(draft)
            self._record_event(
                session,
                "message.deleted",
                conversation_id,
                draft_id,
                payload,
                broadcast=False,
            )
            return payload

    def send_ai_draft(self, draft_id: str):
        with session_scope(self.engine) as session:
            draft = session.get(Message, draft_id)
            if draft is None:
                return None
            metadata = json.loads(draft.metadata_json or "{}")
            if metadata.get("local_draft") is not True:
                return None
            conversation_id = draft.conversation_id
            content = draft.content
            source_message_id = metadata.get("source_message_id")
        sent = self.create_message(
            conversation_id,
            "assistant",
            content,
            metadata={
                "published_from_draft": True,
                "source_message_id": source_message_id,
            },
        )
        if sent is None:
            return None
        self.delete_ai_draft(draft_id)
        return sent

    def _normalize_ai_prompt(self, content: str) -> str:
        prompt = str(content or "").strip()
        if prompt.lower().startswith("@ai"):
            prompt = prompt[3:].lstrip(" ：:")
        return prompt.strip()

    def create_inference_job(
        self,
        conversation_id: str,
        user_message_id: str | None = None,
        prompt: str | None = None,
        assistant_message_id: str | None = None,
    ):
        with session_scope(self.engine) as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return None
            if not user_message_id and not prompt:
                return None

            if assistant_message_id:
                assistant = session.get(Message, assistant_message_id)
            else:
                assistant = Message(
                    conversation_id=conversation_id,
                    sender_id="assistant",
                    sender_name="ParaMind",
                    role="assistant",
                    content="",
                    status="queued",
                    metadata_json=json.dumps({}),
                )
                session.add(assistant)
                session.flush()
                self._record_event(
                    session,
                    "message.created",
                    conversation_id,
                    assistant.id,
                    {"message": self._serialize_message(assistant)},
                )
            assistant_metadata = (
                json.loads(assistant.metadata_json or "{}") if assistant else {}
            )
            broadcast_job_events = assistant_metadata.get("local_draft") is not True

            job = InferenceJob(
                conversation_id=conversation_id,
                user_message_id=user_message_id or "",
                assistant_message_id=assistant.id,
                status="queued",
                model_id=self.settings.model_id,
                family=self.settings.family,
                prompt=prompt,
            )
            session.add(job)
            session.flush()
            if broadcast_job_events:
                conversation.updated_at = utcnow()
                self._record_event(
                    session,
                    "conversation.updated",
                    conversation_id,
                    conversation_id,
                    {
                        "conversation": self._serialize_conversation(
                            session, conversation
                        )
                    },
                    broadcast=True,
                    scope="global",
                )
            return {
                "id": job.id,
                "conversation_id": conversation_id,
                "assistant_message_id": assistant.id,
                "status": job.status,
            }

    def close_conversation(self, conversation_id: str):
        self.process_transport_events()
        with session_scope(self.engine) as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return None
            if conversation.kind == "dm":
                counterpart = next(
                    (
                        peer_id
                        for peer_id in self._conversation_participant_ids(
                            session, conversation_id
                        )
                        if peer_id != self.settings.instance_id
                    ),
                    None,
                )
                messages = session.exec(
                    select(Message).where(Message.conversation_id == conversation_id)
                ).all()
                for message in messages:
                    session.delete(message)
                participants = session.exec(
                    select(Participant).where(
                        Participant.conversation_id == conversation_id
                    )
                ).all()
                for participant in participants:
                    session.delete(participant)
                session.delete(conversation)
                if counterpart:
                    self._upsert_relationship(session, counterpart, "closed_dm")
                payload = {"conversation_id": conversation_id, "kind": "dm"}
                self._record_event(
                    session,
                    "conversation.deleted",
                    None,
                    conversation_id,
                    payload,
                    scope="global",
                )
                return payload

            participant = session.exec(
                select(Participant)
                .where(Participant.conversation_id == conversation_id)
                .where(Participant.peer_id == self.settings.instance_id)
            ).first()
            if participant is not None:
                session.delete(participant)
            remaining = self._conversation_participant_ids(session, conversation_id)
            if not remaining:
                session.delete(conversation)
                payload = {"conversation_id": conversation_id, "kind": "group"}
                self._record_event(
                    session,
                    "conversation.deleted",
                    None,
                    conversation_id,
                    payload,
                    scope="global",
                )
                return payload
            conversation.updated_at = utcnow()
            payload = {
                "conversation": self._serialize_conversation(session, conversation)
            }
            self._record_event(
                session,
                "conversation.updated",
                conversation_id,
                conversation_id,
                payload,
                scope="global",
            )
            return {"conversation_id": conversation_id, "kind": "group"}

    def leave_dm_conversation(self, conversation_id: str):
        return self.close_conversation(conversation_id)

    def create_ai_draft(
        self,
        conversation_id: str,
        prompt: str | None = None,
        source_message_id: str | None = None,
    ):
        resolved_prompt = self._normalize_ai_prompt(prompt or "")
        draft_metadata: dict[str, Any] = {}
        if source_message_id:
            with session_scope(self.engine) as session:
                source_message = session.get(Message, source_message_id)
                if (
                    source_message is None
                    or source_message.conversation_id != conversation_id
                ):
                    return None
                draft_metadata["source_message_id"] = source_message.id
                if not resolved_prompt:
                    resolved_prompt = self._normalize_ai_prompt(source_message.content)
        if not resolved_prompt:
            return None
        assistant = self.create_message(
            conversation_id,
            "assistant",
            "",
            local_draft=True,
            metadata=draft_metadata,
        )
        if assistant is None:
            return None
        job = self.create_inference_job(
            conversation_id,
            user_message_id=source_message_id,
            prompt=resolved_prompt,
            assistant_message_id=assistant["id"],
        )
        if job is None:
            return None
        return {"assistant_message": assistant, "job": job}

    async def enqueue_job(self, job_id: str):
        await self.job_queue.put(job_id)

    async def worker_loop(self):
        while not self.stop_event.is_set():
            try:
                job_id = await asyncio.wait_for(self.job_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            try:
                await self._run_job(job_id)
            finally:
                self.job_queue.task_done()

    async def _run_job(self, job_id: str):
        with session_scope(self.engine) as session:
            job = session.get(InferenceJob, job_id)
            if job is None or job.status == "cancelled":
                return

            route = self.p2p.plan_route(session)
            job.status = "running"
            job.started_at = utcnow()
            job.route_json = json.dumps(route, ensure_ascii=False)
            assistant = session.get(Message, job.assistant_message_id)
            if assistant is None:
                return
            assistant_metadata = json.loads(assistant.metadata_json or "{}")
            broadcast_job_events = assistant_metadata.get("local_draft") is not True
            assistant.status = "streaming"
            assistant.updated_at = utcnow()
            self._record_event(
                session,
                "job.started",
                job.conversation_id,
                job.id,
                {
                    "job_id": job.id,
                    "assistant_message_id": assistant.id,
                    "status": job.status,
                },
                broadcast=broadcast_job_events,
            )
            self._record_event(
                session,
                "job.route",
                job.conversation_id,
                job.id,
                {"job_id": job.id, "route": route},
                broadcast=broadcast_job_events,
            )
            self._record_event(
                session,
                "message.updated",
                job.conversation_id,
                assistant.id,
                {"message": self._serialize_message(assistant)},
                broadcast=broadcast_job_events,
            )
            user_message = (
                session.get(Message, job.user_message_id)
                if job.user_message_id
                else None
            )
            prompt = (
                job.prompt
                if job.prompt
                else (user_message.content if user_message else "")
            )

        try:
            # stream_tokens yields full cumulative text each step (matches InferenceEngine.generate_stream).
            async for cumulative_text in self.inference.stream_tokens(prompt):
                with session_scope(self.engine) as session:
                    job = session.get(InferenceJob, job_id)
                    if job is None:
                        return
                    if job.status == "cancelled":
                        assistant = session.get(Message, job.assistant_message_id)
                        if assistant:
                            assistant_metadata = json.loads(
                                assistant.metadata_json or "{}"
                            )
                            broadcast_job_events = (
                                assistant_metadata.get("local_draft") is not True
                            )
                            assistant.status = "cancelled"
                            assistant.updated_at = utcnow()
                            self._record_event(
                                session,
                                "message.updated",
                                job.conversation_id,
                                assistant.id,
                                {"message": self._serialize_message(assistant)},
                                broadcast=broadcast_job_events,
                            )
                        self._record_event(
                            session,
                            "job.failed",
                            job.conversation_id,
                            job.id,
                            {
                                "job_id": job.id,
                                "assistant_message_id": job.assistant_message_id,
                                "error": "cancelled",
                            },
                            broadcast=broadcast_job_events,
                        )
                        return

                    assistant = session.get(Message, job.assistant_message_id)
                    if assistant is None:
                        return
                    assistant_metadata = json.loads(assistant.metadata_json or "{}")
                    broadcast_job_events = (
                        assistant_metadata.get("local_draft") is not True
                    )
                    assistant.content = cumulative_text
                    assistant.status = "streaming"
                    assistant.updated_at = utcnow()
                    job.status = "streaming"
                    self._record_event(
                        session,
                        "message.token",
                        job.conversation_id,
                        assistant.id,
                        {
                            "message_id": assistant.id,
                            "job_id": job.id,
                            "token": cumulative_text,
                        },
                        broadcast=broadcast_job_events,
                    )

            with session_scope(self.engine) as session:
                job = session.get(InferenceJob, job_id)
                if job is None:
                    return
                assistant = session.get(Message, job.assistant_message_id)
                if assistant is None:
                    return
                assistant_metadata = json.loads(assistant.metadata_json or "{}")
                broadcast_job_events = assistant_metadata.get("local_draft") is not True
                assistant.status = "completed"
                assistant.updated_at = utcnow()
                job.status = "completed"
                job.finished_at = utcnow()
                self._record_event(
                    session,
                    "message.updated",
                    job.conversation_id,
                    assistant.id,
                    {"message": self._serialize_message(assistant)},
                    broadcast=broadcast_job_events,
                )
                self._record_event(
                    session,
                    "job.completed",
                    job.conversation_id,
                    job.id,
                    {
                        "job_id": job.id,
                        "assistant_message_id": assistant.id,
                        "status": job.status,
                    },
                    broadcast=broadcast_job_events,
                )
        except Exception as exc:
            with session_scope(self.engine) as session:
                job = session.get(InferenceJob, job_id)
                if job is None:
                    return
                assistant = session.get(Message, job.assistant_message_id)
                if assistant is not None:
                    assistant_metadata = json.loads(assistant.metadata_json or "{}")
                    broadcast_job_events = (
                        assistant_metadata.get("local_draft") is not True
                    )
                    assistant.status = "failed"
                    assistant.updated_at = utcnow()
                    assistant.metadata_json = json.dumps(
                        {"error": str(exc)}, ensure_ascii=False
                    )
                    self._record_event(
                        session,
                        "message.updated",
                        job.conversation_id,
                        assistant.id,
                        {"message": self._serialize_message(assistant)},
                        broadcast=broadcast_job_events,
                    )
                else:
                    broadcast_job_events = True
                job.status = "failed"
                job.error = str(exc)
                job.finished_at = utcnow()
                self._record_event(
                    session,
                    "job.failed",
                    job.conversation_id,
                    job.id,
                    {
                        "job_id": job.id,
                        "assistant_message_id": job.assistant_message_id,
                        "error": str(exc),
                    },
                    broadcast=broadcast_job_events,
                )

    def cancel_job(self, job_id: str):
        with session_scope(self.engine) as session:
            job = session.get(InferenceJob, job_id)
            if job is None:
                return None
            job.status = "cancelled"
            job.finished_at = utcnow()
            assistant = session.get(Message, job.assistant_message_id)
            assistant_metadata = (
                json.loads(assistant.metadata_json or "{}")
                if assistant is not None
                else {}
            )
            broadcast_job_events = assistant_metadata.get("local_draft") is not True
            self._record_event(
                session,
                "job.failed",
                job.conversation_id,
                job.id,
                {
                    "job_id": job.id,
                    "assistant_message_id": job.assistant_message_id,
                    "error": "cancelled",
                },
                broadcast=broadcast_job_events,
            )
            return {
                "id": job.id,
                "status": job.status,
                "assistant_message_id": job.assistant_message_id,
            }

    def ack_message(self, message_id: str, status: str):
        if status not in {"delivered", "read"}:
            return None
        with session_scope(self.engine) as session:
            message = session.get(Message, message_id)
            if message is None:
                return None
            metadata = json.loads(message.metadata_json or "{}")
            metadata.setdefault("acks", {})[self.settings.instance_id] = status
            message.metadata_json = json.dumps(metadata, ensure_ascii=False)
            if message.role in {"user", "assistant"}:
                status_order = {
                    "draft": 0,
                    "queued": 1,
                    "sent": 2,
                    "delivered": 3,
                    "read": 4,
                    "failed": -1,
                }
                if status_order.get(status, 0) >= status_order.get(message.status, 0):
                    message.status = status
            message.updated_at = utcnow()
            payload = {
                "message_id": message.id,
                "conversation_id": message.conversation_id,
                "status": status,
                "by_peer_id": self.settings.instance_id,
            }
            self._record_event(
                session,
                "message.ack",
                message.conversation_id,
                message.id,
                payload,
            )
            if status == "read":
                self._record_event(
                    session,
                    "message.read",
                    message.conversation_id,
                    message.id,
                    payload,
                )
            self._record_event(
                session,
                "message.updated",
                message.conversation_id,
                message.id,
                {"message": self._serialize_message(message)},
                broadcast=False,
            )
            return payload

    def list_sync_events(self, after: int = 0, limit: int | None = None):
        self.process_transport_events()
        with session_scope(self.engine) as session:
            statement = (
                select(EventLog).where(EventLog.id > after).order_by(EventLog.id)
            )
            if limit:
                statement = statement.limit(limit)
            rows = session.exec(statement).all()
            return {
                "events": [
                    {
                        "id": row.id,
                        "type": row.event_type,
                        "scope": row.scope,
                        "conversation_id": row.conversation_id,
                        "entity_id": row.entity_id,
                        "timestamp": _dt(row.created_at),
                        "payload": json.loads(row.payload_json),
                    }
                    for row in rows
                ]
            }

    def list_outbox(self):
        with session_scope(self.engine) as session:
            rows = session.exec(
                select(Message)
                .where(Message.sender_id == self.settings.instance_id)
                .where(Message.role == "user")
                .order_by(desc(Message.updated_at))
            ).all()
            return [self._serialize_message(row) for row in rows]

    def list_peers(self):
        with session_scope(self.engine) as session:
            self.p2p.sweep_stale_peers(session)
            self.p2p.upsert_self(session)
            return [self._serialize_peer(peer) for peer in self.p2p.list_peers(session)]

    def network_status(self):
        peers = self.list_peers()
        return {
            "instance_id": self.settings.instance_id,
            "instance_name": self.settings.instance_name,
            "transport": self.settings.transport,
            "backend_port": self.settings.backend_port,
            "peer_count": len([peer for peer in peers if peer["status"] == "online"]),
        }

    async def heartbeat_loop(self):
        while not self.stop_event.is_set():
            with session_scope(self.engine) as session:
                action, peer = self.p2p.upsert_self(session)
                self._record_peer_event(session, action, peer)
                stale = self.p2p.sweep_stale_peers(session)
                for item in stale:
                    self._record_event(
                        session,
                        "peer.left",
                        None,
                        item.id,
                        {"peer": self._serialize_peer(item)},
                    )
            self.process_transport_events()
            await asyncio.sleep(1.0)

    def mark_self_offline(self):
        with session_scope(self.engine) as session:
            peer = self.p2p.mark_self_offline(session)
            if peer is not None:
                self._record_event(
                    session,
                    "peer.left",
                    None,
                    peer.id,
                    {"peer": self._serialize_peer(peer)},
                )

    def get_events(self, conversation_id: str, after: int, limit: int | None):
        self.process_transport_events()
        with session_scope(self.engine) as session:
            statement = (
                select(EventLog)
                .where(EventLog.id > after)
                .where(EventLog.scope == "conversation")
                .where(EventLog.conversation_id == conversation_id)
                .order_by(EventLog.id)
            )
            if limit:
                statement = statement.limit(limit)
            rows = session.exec(statement).all()

            return [
                {
                    "id": row.id,
                    "type": row.event_type,
                    "scope": row.scope,
                    "conversation_id": row.conversation_id,
                    "entity_id": row.entity_id,
                    "timestamp": _dt(row.created_at),
                    "payload": json.loads(row.payload_json),
                }
                for row in rows
            ]

    def get_global_events(self, after: int, limit: int | None):
        self.process_transport_events()
        with session_scope(self.engine) as session:
            statement = (
                select(EventLog)
                .where(EventLog.id > after)
                .where(EventLog.scope == "global")
                .order_by(EventLog.id)
            )
            if limit:
                statement = statement.limit(limit)
            rows = session.exec(statement).all()
            return [
                {
                    "id": row.id,
                    "type": row.event_type,
                    "scope": row.scope,
                    "conversation_id": row.conversation_id,
                    "entity_id": row.entity_id,
                    "timestamp": _dt(row.created_at),
                    "payload": json.loads(row.payload_json),
                }
                for row in rows
            ]
