"""Adapters: per-tenant mapping files compiled to SQL, and the extract into canonical snapshots.

Modules: mapping (load/validate), compiler (to SQL), extract (into a snapshot), check (onboarding report).
No re-exports here, so `python -m submissions.adapters.<module>` runs cleanly.
"""
