"""Register a new NBFC: tenant row + mapping file from the template (+ optional placeholder bureau profiles).

Usage:  python -m submissions.onboarding.new_tenant --tenant nbfc_017 --name "Acme Finance" --schema nbfc_017
                                                   [--strategy stream|insert_select] [--placeholder-bureaus]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import Engine

from submissions.bureaus import BureauRepository
from submissions.bureaus.seed import placeholder_profiles
from submissions.enums import ExtractStrategy
from submissions.tenants import Tenant, TenantRepository

TEMPLATE = Path("clients/_template/mapping.yaml")


def register_tenant(engine: Engine, base_dir: Path, tenant_id: str, name: str, schema: str,
                    strategy: ExtractStrategy | str = ExtractStrategy.STREAM,
                    placeholder_bureaus: bool = False) -> tuple[Tenant, Path, bool]:
    """Create/update the tenant and create its mapping file if missing. Returns (tenant, mapping path, created)."""
    mapping_rel = Path("clients") / tenant_id / "mapping.yaml"
    tenant = Tenant(tenant_id, name, schema, mapping_rel.as_posix(), ExtractStrategy(strategy))  # validates ids
    mapping_path = base_dir / mapping_rel
    created = not mapping_path.exists()
    if created:
        mapping_path.parent.mkdir(parents=True, exist_ok=True)
        template = (base_dir / TEMPLATE).read_text(encoding="utf-8")
        mapping_path.write_text(template.replace("<tenant_id>", tenant_id).replace("<schema>", schema),
                                encoding="utf-8", newline="\n")
    tenants = TenantRepository(engine)
    tenants.upsert(tenant)
    if placeholder_bureaus:
        repo = BureauRepository(engine)
        number = len(tenants.list(active_only=False))
        for profile in placeholder_profiles(tenant_id, number, name.upper()):
            repo.upsert(profile)
    return tenant, mapping_path, created


def main(argv: list[str] | None = None) -> int:
    from submissions.config import Settings
    from submissions.db import make_engine

    ap = argparse.ArgumentParser(description="Register a new NBFC tenant")
    ap.add_argument("--tenant", required=True, help="tenant id, e.g. nbfc_017 (lowercase, digits, underscore)")
    ap.add_argument("--name", required=True, help="display name")
    ap.add_argument("--schema", required=True, help="the NBFC's schema in the LMS database")
    ap.add_argument("--strategy", default="stream", choices=[s.value for s in ExtractStrategy])
    ap.add_argument("--placeholder-bureaus", action="store_true",
                    help="add PLACEHOLDER bureau profiles (replace with the real member IDs before generating files)")
    args = ap.parse_args(argv)
    tenant, path, created = register_tenant(make_engine(Settings.from_env().database_url), Path.cwd(), args.tenant,
                                            args.name, args.schema, args.strategy, args.placeholder_bureaus)
    print(f"Tenant {tenant.tenant_id} registered (schema {tenant.source_schema}, {tenant.extract_strategy}).")
    print(f"Mapping file: {path} ({'created from template' if created else 'already existed, left unchanged'})")
    print("Next: python -m submissions.onboarding.introspect --schema", tenant.source_schema)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
