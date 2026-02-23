from sqlalchemy import Column, Integer, String, Float, JSON, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship, declarative_base
import datetime

Base = declarative_base()

class Endpoint(Base):
    __tablename__ = "endpoints"
    
    id = Column(Integer, primary_key=True)
    method = Column(String, nullable=False)
    path_pattern = Column(String, nullable=False) # Normalized path, e.g., /users/{id}
    target_url = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    behavior = relationship("EndpointBehavior", back_populates="endpoint", uselist=False)
    chaos = relationship("ChaosConfig", back_populates="endpoint", uselist=False)

class EndpointBehavior(Base):
    __tablename__ = "endpoint_behavior"
    
    id = Column(Integer, primary_key=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"), unique=True)
    
    # Latency (ms)
    latency_mean = Column(Float, default=400.0)
    latency_std = Column(Float, default=100.0)
    
    # Error rates and status codes
    error_rate = Column(Float, default=0.0)
    status_code_distribution = Column(JSON, nullable=True) # Map of code -> probability
    
    # Schema info
    response_schema = Column(JSON, nullable=True) # Representative response body/structure
    request_schema = Column(JSON, nullable=True) # Representative request body/structure
    
    endpoint = relationship("Endpoint", back_populates="behavior")

class ChaosConfig(Base):
    __tablename__ = "chaos_config"
    
    id = Column(Integer, primary_key=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"), unique=True)
    
    chaos_level = Column(Integer, default=0) # 0-100
    is_active = Column(Boolean, default=False)
    
    endpoint = relationship("Endpoint", back_populates="chaos")

class ContractDrift(Base):
    __tablename__ = "contract_drift"
    
    id = Column(Integer, primary_key=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"))
    
    # Drift metadata
    detected_at = Column(DateTime, default=datetime.datetime.utcnow)
    drift_score = Column(Float, default=0.0)  # 0-100 severity score
    drift_summary = Column(String, nullable=True)  # Human-readable summary
    drift_details = Column(JSON, nullable=True)  # Full list of drift issues
    
    # Status tracking
    is_resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)
    
    endpoint = relationship("Endpoint")

class HealthMetric(Base):
    __tablename__ = "health_metrics"
    
    id = Column(Integer, primary_key=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"))
    
    # Snapshot timestamp
    recorded_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # Measurements
    latency_ms = Column(Float, default=0.0)
    status_code = Column(Integer, default=200)
    response_size_bytes = Column(Integer, default=0)
    is_error = Column(Boolean, default=False)
    
    # Anomaly flags (set by HealthMonitor)
    latency_anomaly = Column(Boolean, default=False)
    error_spike = Column(Boolean, default=False)
    size_anomaly = Column(Boolean, default=False)
    
    # Overall health score for this observation (0-100, 100=healthy)
    health_score = Column(Float, default=100.0)
    anomaly_reasons = Column(JSON, nullable=True)  # List of anomaly reason strings
    
    endpoint = relationship("Endpoint")

