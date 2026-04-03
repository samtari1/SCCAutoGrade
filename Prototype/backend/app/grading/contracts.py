from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List


class QuestionType(str, Enum):
    CODE = "code"
    ESSAY = "essay"
    NUMERIC = "numeric"
    MCQ = "mcq"
    COMPOSITE = "composite"


@dataclass
class ConfidenceReport:
    value: float
    source: str
    reasons: List[str] = field(default_factory=list)


@dataclass
class GradingRequest:
    job_id: str
    evaluator_key: str
    submission_archive_path: Path
    instructions_path: Path
    output_dir: Path
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GradingResult:
    job_id: str
    evaluator_key: str
    status: str
    artifact_files: List[str]
    confidence: ConfidenceReport
    details: Dict[str, Any] = field(default_factory=dict)


class BaseEvaluator:
    key: str = "base"
    supported_types: List[QuestionType] = [QuestionType.COMPOSITE]

    def grade(self, request: GradingRequest) -> GradingResult:
        raise NotImplementedError
