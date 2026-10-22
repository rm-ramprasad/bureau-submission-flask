"""UCRF consumer pipeline: tenant mapping -> canonical snapshot -> UCRF rule pack -> UCRF file per bureau."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from submissions.adapters.extract import run_extract
from submissions.adjustments.summary import adjustments_csv
from submissions.adapters.mapping import load_mapping
from submissions.bureaus import BureauProfile
from submissions.formats.ucrf_consumer.writer import build_file
from submissions.pipeline.base import Services
from submissions.requests import RequestResult, SubmissionRequest
from submissions.snapshot import Snapshot
from submissions.tenants import Tenant
from submissions.validation.engine import ValidationResult, validate_snapshot
from submissions.validation.results import FindingFilter, findings_csv
from submissions.validation.rules import load_rule_set


class ConsumerPipeline:
    def build_snapshot(self, services: Services, tenant: Tenant, request: SubmissionRequest,
                       owner: str) -> tuple[int, ValidationResult]:
        if not tenant.mapping_path:
            raise ValueError(f"Tenant {tenant.tenant_id} has no mapping file configured")
        mapping = load_mapping(services.base_dir / tenant.mapping_path)
        extract = run_extract(tenant=tenant, mapping=mapping, submission_type=request.submission_type,
                              as_of_date=request.as_of_date, app_engine=services.app_engine,
                              source_engine=services.source_engine, snapshots=services.snapshots, owner=owner,
                              batch_id=request.batch_id)
        rules = load_rule_set(services.app_engine, tenant.tenant_id, request.submission_type)
        result = validate_snapshot(services.app_engine, services.snapshots, extract.snapshot_id, rules)
        return extract.snapshot_id, result

    def generate_file(self, services: Services, tenant: Tenant, request: SubmissionRequest, snapshot: Snapshot,
                      bureau: BureauProfile) -> RequestResult:
        content, stats = build_file(services.app_engine, snapshot.snapshot_id, snapshot.as_of_date, bureau,
                                    request.reporting_cycle)
        if stats.written == 0:
            raise ValueError(f"No accepted records to submit: all {stats.total_records} records were rejected by "
                             f"validation. Review the findings of snapshot {snapshot.snapshot_id} (no file written).")
        name = bureau.file_name(datetime.now(), snapshot.as_of_date)
        if Path(name).name != name:
            raise ValueError(f"File name {name!r} from the bureau profile must not contain a path")
        folder = f"{tenant.tenant_id}/{request.batch_id}"
        file_uri = services.store.put(f"{folder}/{name}", content, "text/plain")   # never overwrites
        csv_text = ("".join(findings_csv(services.app_engine, snapshot.snapshot_id, FindingFilter()))
                    + adjustments_csv(services.app_engine, snapshot.snapshot_id))
        exceptions_uri = services.store.put(f"{folder}/{Path(name).stem}.exceptions.csv",
                                            csv_text.encode("utf-8"), "text/csv")
        return RequestResult(output_file_name=name, output_file_path=file_uri,
                             exceptions_file_path=exceptions_uri, total_records=stats.total_records,
                             accepted_records=stats.written, rejected_records=stats.rejected,
                             duplicate_records=stats.duplicates)
