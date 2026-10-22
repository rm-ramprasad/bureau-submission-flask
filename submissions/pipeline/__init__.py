"""Pipelines per submission type and their registry."""

from __future__ import annotations

from submissions.enums import SubmissionType
from submissions.pipeline.base import FileGenerationUnavailable, Services, SubmissionPipeline
from submissions.pipeline.consumer import ConsumerPipeline

REGISTRY: dict[SubmissionType, SubmissionPipeline] = {
    SubmissionType.CONSUMER: ConsumerPipeline(),
    # COMMERCIAL, NESL: plug in when their specs are built
}


def get_pipeline(submission_type: SubmissionType | str) -> SubmissionPipeline | None:
    return REGISTRY.get(SubmissionType(submission_type))


def enabled_types() -> list[SubmissionType]:
    """Submission types the UI may offer."""
    return list(REGISTRY)


__all__ = ["FileGenerationUnavailable", "REGISTRY", "Services", "SubmissionPipeline", "enabled_types", "get_pipeline"]
