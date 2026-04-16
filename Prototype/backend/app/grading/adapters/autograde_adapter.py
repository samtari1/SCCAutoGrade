from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from ..legacy import AutoGrader


@dataclass
class AutoGradeRunConfig:
    evaluator_key: str
    route_type: str
    language_hint: str


class AutoGradeAdapter:
    """Adapter that runs backend-owned AutoGrader core with configurable runtime options."""

    def __init__(self, config: AutoGradeRunConfig):
        self.config = config

    def _collect_artifacts(self, output_dir: Path) -> List[str]:
        return sorted([p.name for p in output_dir.glob("*") if p.is_file()])

    def run(
        self,
        submission_archive_path: Path,
        instructions_path: Path,
        output_dir: Path,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        grader = AutoGrader(
            api_key=metadata.get("openai_api_key"),
            use_custom_endpoint=metadata.get("model_provider") in {"custom", "ollama", "local"}
            if metadata.get("model_provider")
            else None,
            custom_endpoint=metadata.get("custom_endpoint"),
            model_provider=metadata.get("model_provider"),
            model_name=metadata.get("model_name"),
        )

        # Preserve multi-agent configuration expected by the ported core.
        if hasattr(grader, "enable_multi_agent_grading"):
            grader.enable_multi_agent_grading = bool(metadata.get("multi_agent_grading", True))
        if hasattr(grader, "multi_agent_disagreement_threshold"):
            grader.multi_agent_disagreement_threshold = float(
                metadata.get("multi_agent_disagreement_threshold", 5.0)
            )
        if hasattr(grader, "multi_agent_part_disagreement_threshold"):
            grader.multi_agent_part_disagreement_threshold = float(
                metadata.get("multi_agent_part_disagreement_threshold", 10.0)
            )

        # Keep language/profile hints available for future specialization paths.
        if hasattr(grader, "language_hint"):
            grader.language_hint = self.config.language_hint
        if hasattr(grader, "grading_profile"):
            grader.grading_profile = self.config.route_type
        if hasattr(grader, "grading_context"):
            grader.grading_context = str(metadata.get("grading_context") or "")

        # Support resuming interrupted jobs by skipping already-completed students
        completed_students = metadata.get("completed_students") or []
        if hasattr(grader, "completed_students"):
            grader.completed_students = completed_students
        selected_students = metadata.get("selected_students") or []
        if hasattr(grader, "selected_students"):
            grader.selected_students = selected_students
        if hasattr(grader, "cancel_check"):
            grader.cancel_check = metadata.get("cancel_check")
        if hasattr(grader, "progress_callback"):
            grader.progress_callback = metadata.get("progress_callback")

        output_dir.mkdir(parents=True, exist_ok=True)
        grader.grade_all_assignments(
            str(submission_archive_path),
            str(instructions_path),
            str(output_dir),
        )

        return {
            "status": "stopped" if getattr(grader, "last_run_stopped", False) else "finished",
            "artifact_files": self._collect_artifacts(output_dir),
            "confidence": {
                "value": 0.9,
                "source": "ported_autograder_core",
                "reasons": [
                    "Uses backend-owned port of the original AutoGrade logic for full report quality parity.",
                    f"Requested specialization: route={self.config.route_type}, language={self.config.language_hint}.",
                ],
            },
        }
