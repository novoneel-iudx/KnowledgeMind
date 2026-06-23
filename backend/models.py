from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, Literal
from uuid import uuid4
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return str(uuid4())


class TeamMember(BaseModel):
    member_id: str = Field(default_factory=_uid)
    handle: str
    display_name: str


class NodeSource(BaseModel):
    type: Literal["sow", "chat_message"]
    ref: str  # SoW excerpt or message_id


class KGNode(BaseModel):
    id: str
    label: str
    plane: Literal["origin", "user"]
    type: str
    description: str
    source: NodeSource
    coverage_status: Literal["unaddressed", "partial", "covered"] = "unaddressed"


class KGEdge(BaseModel):
    id: str = Field(default_factory=_uid)
    source: str
    target: str
    plane: Literal["origin", "user", "cross"]
    relation: str


class Rule(BaseModel):
    rule_id: str = Field(default_factory=_uid)
    name: str
    salience: int = 10
    when: str
    then: str
    sow_excerpt: str = ""
    violation_status: Literal["ok", "at_risk", "violated"] = "ok"


class MessageAuthor(BaseModel):
    member_id: str
    handle: str


class MessageMetadata(BaseModel):
    alignment_score: int = 0
    in_scope: list[str] = []
    out_of_scope: list[str] = []
    deviations: list[str] = []
    coverage_delta: list[str] = []
    recommendations: list[str] = []
    suggested_tags: list[str] = []
    user_plane_nodes_created: list[str] = []
    cross_plane_edges_created: list[str] = []


class ChatMessage(BaseModel):
    message_id: str = Field(default_factory=_uid)
    role: Literal["user", "assistant"]
    author: Optional[MessageAuthor] = None
    content: str
    tags: list[str] = []
    timestamp: str = Field(default_factory=_now)
    metadata: MessageMetadata = Field(default_factory=MessageMetadata)


class Project(BaseModel):
    project_id: str = Field(default_factory=_uid)
    name: str
    sow_text: str
    created_at: str = Field(default_factory=_now)
    members: list[TeamMember] = []
