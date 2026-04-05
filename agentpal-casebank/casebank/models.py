"""Core data models for CaseBank files."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


TaskTerminalStatus = Literal["done", "failed", "cancelled"]
CaseState = Literal["candidate", "gold"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RawEvent(BaseModel):
    """Normalized raw event persisted in JSONL."""

    ingest_id: str = Field(default_factory=lambda: str(uuid4()))
    observed_at: str = Field(default_factory=utc_now_iso)
    source: str
    entity_type: str
    entity_id: Optional[str] = None
    source_event_id: Optional[str] = None
    event_time: Optional[str] = None
    payload_hash: str
    payload: Dict[str, Any]


class CaseRecord(BaseModel):
    """Curated candidate or gold case persisted as JSON."""

    case_id: str
    state: CaseState
    source: Literal["prod", "manual", "benchmark"] = "prod"
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    input_snapshot: Dict[str, Any] = Field(default_factory=dict)
    timeline_refs: List[Dict[str, Any]] = Field(default_factory=list)
    expected_outcome: Optional[str] = None
    expected_tools: Optional[List[str]] = None
    labels: List[str] = Field(default_factory=list)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    created_at: str = Field(default_factory=utc_now_iso)
    promoted_at: Optional[str] = None
    reviewer: Optional[str] = None


class RunMeta(BaseModel):
    """Evaluation run metadata."""

    run_id: str
    suite: str
    started_at: str = Field(default_factory=utc_now_iso)
    ended_at: Optional[str] = None


class CaseResult(BaseModel):
    """Per-case evaluation output."""

    run_id: str
    case_id: str
    task_success: Optional[bool] = None
    execution_accuracy: Optional[float] = None
    tool_path_match_rate: Optional[float] = None
    tool_accuracy: Optional[float] = None
    recovered: Optional[bool] = None
    mttr_seconds: Optional[float] = None
    notes: List[str] = Field(default_factory=list)


class MetricsSummary(BaseModel):
    """Aggregated KPI summary."""

    run_id: str
    task_success_rate: float
    execution_accuracy: float
    tool_path_match_rate: Optional[float] = None
    tool_accuracy: float
    incident_rate_per_100_tasks: float
    recovery_rate: float
    mttr_seconds: Optional[float] = None
    stability_score: float
    sample_size_tasks: int
    sample_size_tool_calls: int
