-- 007: exceptions belong to a request. The New request form creates a DRAFT request; the user captures its
-- exclusions and overrides (tagged with its batch_id), then submits it for validation, which locks them.
-- submission_request.status gains the value DRAFT (VARCHAR(12), no schema change needed).

ALTER TABLE submission_adjustment
    ADD COLUMN batch_id VARCHAR(40) AFTER adjustment_id,
    ADD KEY ix_submission_adjustment_batch (batch_id);
