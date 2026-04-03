from typing import List, Optional

from pydantic import BaseModel, Field


class ConfidenceResponse(BaseModel):
    value: float
    source: str
    reasons: List[str] = Field(default_factory=list)


class CreateJobResponse(BaseModel):
    job_id: str
    status: str
    evaluator_key: str
    route_type: Optional[str] = None
    routing_reason: Optional[str] = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    message: Optional[str] = None
    error: Optional[str] = None
    evaluator_key: Optional[str] = None
    route_type: Optional[str] = None
    routing_reason: Optional[str] = None
    artifact_files: List[str] = Field(default_factory=list)
    confidence: Optional[ConfidenceResponse] = None
