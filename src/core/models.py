"""
ORM Models
==========
All SQLAlchemy table definitions for the platform.

Column notes:
  - JSON columns use postgresql_as_jsonb=True so that on Postgres they are stored
    as JSONB (binary JSON — faster queries, supports GIN indexing).  On SQLite the
    hint is silently ignored and plain TEXT storage is used.
  - Timestamps use server_default=func.now() so the DB engine fills them in; this
    is safer than Python-side datetime.utcnow() across distributed workers.
"""

import datetime

from sqlalchemy import Column, Integer, String, Float, JSON, Boolean, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class Endpoint(Base):
    __tablename__ = "endpoints"

    id          = Column(Integer, primary_key=True)
    method      = Column(String, nullable=False)
    path_pattern = Column(String, nullable=False)   # Normalised, e.g. /users/{id}
    target_url  = Column(String, nullable=False)
    created_at  = Column(DateTime, default=datetime.datetime.utcnow, server_default=func.now())

    # Prevent duplicate (method, path_pattern) rows from race conditions
    __table_args__ = (
        UniqueConstraint("method", "path_pattern", name="uq_endpoint_method_path"),
    )

    behavior = relationship("EndpointBehavior", back_populates="endpoint", uselist=False)
    chaos    = relationship("ChaosConfig",      back_populates="endpoint", uselist=False)


class EndpointBehavior(Base):
    __tablename__ = "endpoint_behavior"

    id          = Column(Integer, primary_key=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"), unique=True)

    # Latency (ms)
    latency_mean = Column(Float, default=400.0)
    latency_std  = Column(Float, default=100.0)

    # Error rates and status codes
    error_rate              = Column(Float, default=0.0)
    status_code_distribution = Column(JSON, nullable=True)

    # Schema info
    response_schema = Column(JSON, nullable=True)
    request_schema  = Column(JSON, nullable=True)

    endpoint = relationship("Endpoint", back_populates="behavior")


class ChaosConfig(Base):
    __tablename__ = "chaos_config"

    id          = Column(Integer, primary_key=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"), unique=True)

    chaos_level = Column(Integer, default=0)    # 0–100
    is_active   = Column(Boolean, default=False)

    endpoint = relationship("Endpoint", back_populates="chaos")


class ContractDrift(Base):
    __tablename__ = "contract_drift"

    id          = Column(Integer, primary_key=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"))

    # Drift metadata
    detected_at  = Column(DateTime, default=datetime.datetime.utcnow, server_default=func.now())
    drift_score  = Column(Float, default=0.0)      # 0–100 severity score
    drift_summary = Column(String, nullable=True)   # Human-readable summary
    drift_details = Column(JSON, nullable=True)

    # Status tracking
    is_resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)

    endpoint = relationship("Endpoint")


class HealthMetric(Base):
    __tablename__ = "health_metrics"

    id          = Column(Integer, primary_key=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"))

    # Snapshot timestamp
    recorded_at = Column(DateTime, default=datetime.datetime.utcnow, server_default=func.now())

    # Measurements
    latency_ms          = Column(Float,   default=0.0)
    status_code         = Column(Integer, default=200)
    response_size_bytes = Column(Integer, default=0)
    is_error            = Column(Boolean, default=False)

    # Anomaly flags (set by HealthMonitor)
    latency_anomaly = Column(Boolean, default=False)
    error_spike     = Column(Boolean, default=False)
    size_anomaly    = Column(Boolean, default=False)

    # Overall health score for this observation (0–100, 100=healthy)
    health_score    = Column(Float, default=100.0)
    anomaly_reasons = Column(JSON, nullable=True)

    endpoint = relationship("Endpoint")
