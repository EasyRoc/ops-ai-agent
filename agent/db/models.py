import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Float, Text, DateTime, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(String(64), primary_key=True, default=lambda: f"INC-{uuid.uuid4().hex[:12].upper()}")
    service = Column(String(128), nullable=False)
    env = Column(String(32), nullable=False, default="prod")
    severity = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False, default="open")
    alert_name = Column(String(256))
    alert_value = Column(String(128))
    root_cause = Column(Text)
    confidence = Column(Float)
    evidence = Column(JSONB)
    runbook_name = Column(String(128))
    action_plan = Column(JSONB)
    risk_assessment = Column(JSONB)
    approval_status = Column(String(32))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Execution(Base):
    __tablename__ = "executions"

    id = Column(Integer, primary_key=True)
    incident_id = Column(String(64), ForeignKey("incidents.id"))
    action = Column(String(512), nullable=False)
    operator = Column(String(64))
    status = Column(String(32), nullable=False, default="pending")
    result = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True))


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    incident_id = Column(String(64), ForeignKey("incidents.id"))
    content = Column(Text, nullable=False)
    fault_patterns = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    incident_id = Column(String(64))
    actor = Column(String(64), nullable=False)
    action = Column(String(128), nullable=False)
    detail = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
