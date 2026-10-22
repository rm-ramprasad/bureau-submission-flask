"""File store tests. S3 is exercised with an in-memory fake client that behaves like S3 for the calls we use."""

import io
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from submissions.bureaus.seed import seed as seed_bureaus
from submissions.config import Settings
from submissions.enums import RequestStatus as S, RunMode as M
from submissions.ops.retention import purge
from submissions.pipeline import Services
from submissions.snapshot import SnapshotRepository
from submissions.storage import (
    LocalFileStore, S3FileStore, S3Location, StoredFileExists, StoredFileNotFound, make_store,
)
from submissions.web import create_app
from submissions.worker.runner import Worker

ROOT = Path(__file__).resolve().parent.parent
AS_OF = date(2026, 9, 30)


class _Body(io.BytesIO):
    def iter_chunks(self, chunk_size=1024):
        while chunk := self.read(chunk_size):
            yield chunk

    def __enter__(self):                     # like botocore's StreamingBody: `with` yields the raw stream
        return io.BytesIO(self.getvalue())


class FakeS3:
    """put_object (incl. IfNoneMatch='*'), get_object, delete_object; records the call arguments."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.puts: list[dict] = []

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch=None, **kwargs):
        if IfNoneMatch == "*" and (Bucket, Key) in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed", "Message": "exists"}}, "PutObject")
        self.objects[(Bucket, Key)] = bytes(Body)
        self.puts.append({"Bucket": Bucket, "Key": Key, "IfNoneMatch": IfNoneMatch, **kwargs})
        return {}

    def get_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": _Body(self.objects[(Bucket, Key)])}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
        return {}


@pytest.fixture
def s3():
    return FakeS3()


@pytest.fixture
def store(s3):
    return S3FileStore(s3, "bureau-files", "bureau-submissions")


# ---------------------------------------------------------------- stores
def test_s3_put_read_delete(store, s3):
    uri = store.put("nbfc_001/b1/CIB0000001-01102026-090000.txt", b"HD...TRLR", "text/plain")
    assert uri == "s3://bureau-files/bureau-submissions/nbfc_001/b1/CIB0000001-01102026-090000.txt"
    put = s3.puts[0]
    assert (put["IfNoneMatch"], put["ServerSideEncryption"], put["ContentType"]) == ("*", "AES256", "text/plain")
    assert b"".join(store.read_chunks(uri)) == b"HD...TRLR"
    store.delete(uri)
    with pytest.raises(StoredFileNotFound):
        list(store.read_chunks(uri))


def test_s3_never_overwrites(store):
    store.put("t/b/f.txt", b"one", "text/plain")
    with pytest.raises(StoredFileExists):
        store.put("t/b/f.txt", b"two", "text/plain")


def test_s3_kms_encryption(s3):
    S3FileStore(s3, "bucket", "p", kms_key_id="alias/bureau").put("t/b/f.txt", b"x", "text/plain")
    assert (s3.puts[0]["ServerSideEncryption"], s3.puts[0]["SSEKMSKeyId"]) == ("aws:kms", "alias/bureau")


def test_s3_owns_only_its_bucket_and_prefix(store):
    assert store.owns("s3://bureau-files/bureau-submissions/t/b/f.txt")
    assert not store.owns("s3://other-bucket/bureau-submissions/t/b/f.txt")
    assert not store.owns("s3://bureau-files/elsewhere/f.txt")
    assert not store.owns("C:/output/t/b/f.txt")
    with pytest.raises(StoredFileNotFound):
        list(store.read_chunks("s3://other-bucket/bureau-submissions/x"))


@pytest.mark.parametrize("bad", ["", "../x.txt", "t/../../x", "t//x", "./x"])
def test_keys_cannot_escape(store, tmp_path, bad):
    for s in (store, LocalFileStore(tmp_path)):
        with pytest.raises(ValueError):
            s.put(bad, b"x", "text/plain")


def test_local_store(tmp_path):
    local = LocalFileStore(tmp_path)
    uri = local.put("t/b/f.txt", b"abc", "text/plain")
    assert b"".join(local.read_chunks(uri)) == b"abc" and local.owns(uri)
    with pytest.raises(StoredFileExists):
        local.put("t/b/f.txt", b"again", "text/plain")
    assert not local.owns(str(tmp_path.parent / "secret.txt"))


def test_s3_location_parsing():
    assert S3Location.parse("s3://b/k/x.txt") == S3Location("b", "k/x.txt")
    with pytest.raises(ValueError):
        S3Location.parse("https://b/k")


def test_make_store_requires_bucket_for_s3(tmp_path):
    with pytest.raises(RuntimeError, match="SUBMISSIONS_S3_BUCKET"):
        make_store(Settings("sqlite://", "sqlite://", tmp_path, storage="s3"))
    assert isinstance(make_store(Settings("sqlite://", "sqlite://", tmp_path)), LocalFileStore)


# ---------------------------------------------------------------- end to end on S3
@pytest.fixture
def worker(engine, lms_engine, requests_repo, tenant_repo, bureau_repo, store):
    seed_bureaus(bureau_repo, ["nbfc_001"])
    services = Services(app_engine=engine, source_engine=lms_engine, snapshots=SnapshotRepository(engine),
                        base_dir=ROOT, store=store)
    return Worker(services, requests_repo, tenant_repo, bureau_repo, worker_id="w-s3")


def generate(worker, requests_repo):
    v = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER"])
    worker.run_once()
    g = requests_repo.create_batch("nbfc_001", AS_OF, M.GENERATE, ["CONSUMER"], ["CIBIL"],
                                   snapshot_id=requests_repo.list_batch(v)[0].snapshot_id)
    worker.run_once()
    (row,) = requests_repo.list_batch(g)
    return row


def test_generated_files_go_to_s3_and_uri_is_stored(worker, requests_repo, s3):
    row = generate(worker, requests_repo)
    assert row.status is S.SUCCEEDED, row.error_message
    assert re.fullmatch(r"s3://bureau-files/bureau-submissions/nbfc_001/[^/]+/CIB0000001-\d{8}-\d{6}\.txt",
                        row.output_file_path)
    assert row.exceptions_file_path.endswith(".exceptions.csv") and row.exceptions_file_path.startswith("s3://")
    content = s3.objects[tuple(S3Location.parse(row.output_file_path).__dict__.values())]
    assert content.startswith(b"TUDF12CIB0000001") and content.endswith(b"TRLR")


def test_ui_download_streams_from_s3(worker, requests_repo, engine, store, s3, tmp_path):
    row = generate(worker, requests_repo)
    app = create_app(Settings("sqlite://", "sqlite://", tmp_path, secret_key="t"), engine=engine, store=store)
    client = app.test_client()
    resp = client.get(f"/requests/{row.request_id}/file")
    assert resp.status_code == 200 and resp.get_data().startswith(b"TUDF12")
    assert row.output_file_name in resp.headers["Content-Disposition"]
    csv = client.get(f"/requests/{row.request_id}/exceptions")
    assert csv.status_code == 200 and csv.mimetype == "text/csv" and csv.get_data().startswith(b"rule_id,")
    s3.objects.clear()                                                  # object gone from the bucket
    assert client.get(f"/requests/{row.request_id}/file").status_code == 404


def test_download_explains_storage_mismatch(worker, requests_repo, engine, tmp_path):
    row = generate(worker, requests_repo)                              # written to S3
    local_app = create_app(Settings("sqlite://", "sqlite://", tmp_path, secret_key="t"), engine=engine)  # web on local
    resp = local_app.test_client().get(f"/requests/{row.request_id}/file")
    text = resp.get_data(as_text=True)
    assert resp.status_code == 404 and "s3://bureau-files/" in text and "local folder" in text
    assert "SUBMISSIONS_STORAGE" in text


def test_s3_configured_app_still_serves_locally_written_files(worker, requests_repo, engine, store, tmp_path):
    from submissions.requests import RequestResult
    from submissions.storage import ReadableStores
    local = LocalFileStore(tmp_path / "output")
    legacy_uri = local.put("nbfc_001/old-batch/CIB0000001-01092026-090000.txt", b"TUDF12-legacy", "text/plain")
    requests_repo.create_batch("nbfc_001", AS_OF, M.GENERATE, ["CONSUMER"], ["CIBIL"], snapshot_id=1)
    req = requests_repo.claim_next("w")
    requests_repo.mark_succeeded(req.request_id, RequestResult(output_file_name="legacy.txt",
                                                               output_file_path=legacy_uri))
    both = ReadableStores(store, local)
    app = create_app(Settings("sqlite://", "sqlite://", tmp_path, secret_key="t"), engine=engine, store=both)
    resp = app.test_client().get(f"/requests/{req.request_id}/file")
    assert resp.status_code == 200 and resp.get_data() == b"TUDF12-legacy"
    assert both.put("t/b/new.txt", b"n", "text/plain").startswith("s3://")       # new files still go to S3


def test_make_store_s3_reads_local_folder_too(tmp_path, monkeypatch):
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: FakeS3())
    store = make_store(Settings("sqlite://", "sqlite://", tmp_path, storage="s3", s3_bucket="opta-s3-demo"))
    assert store.describe() == f"s3://opta-s3-demo/bureau-submissions/ or local folder {tmp_path.resolve()}"
    assert store.owns(str(tmp_path / "x.txt")) and store.owns("s3://opta-s3-demo/bureau-submissions/a/b.txt")


def test_retention_deletes_s3_objects(worker, requests_repo, engine, store, s3):
    row = generate(worker, requests_repo)
    assert len(s3.objects) == 2
    purge(engine, timedelta(days=90), store, clock=lambda: datetime.now() + timedelta(days=200))
    assert s3.objects == {} and requests_repo.get(row.request_id).output_file_path is None
