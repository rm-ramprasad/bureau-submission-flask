"""Pipeline contract per submission type, and the shared services a pipeline uses.

A pipeline knows how to turn a tenant's source data into a validated snapshot and how to write a bureau
file from it. The worker only orchestrates; it never knows about mappings, rules or file layouts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine

from submissions.bureaus import BureauProfile
from submissions.requests import RequestResult, SubmissionRequest
from submissions.snapshot import Snapshot, SnapshotRepository
from submissions.storage import FileStore
from submissions.tenants import Tenant
from submissions.validation.engine import ValidationResult


@dataclass(frozen=True)
class Services:
    app_engine: Engine
    source_engine: Engine
    snapshots: SnapshotRepository
    base_dir: Path              # tenant mapping paths are relative to this
    store: FileStore            # where generated files are written (S3 in real use)


class FileGenerationUnavailable(Exception):
    """The submission type can validate but cannot write files yet."""


class SubmissionPipeline(Protocol):
    def build_snapshot(self, services: Services, tenant: Tenant, request: SubmissionRequest,
                       owner: str) -> tuple[int, ValidationResult]:
        """Extract and validate. Must create the snapshot keyed by the request's batch (SnapshotExistsError
        if another worker already did) and leave it VALIDATED."""

    def generate_file(self, services: Services, tenant: Tenant, request: SubmissionRequest, snapshot: Snapshot,
                      bureau: BureauProfile) -> RequestResult:
        """Write one bureau file from a VALIDATED snapshot."""
