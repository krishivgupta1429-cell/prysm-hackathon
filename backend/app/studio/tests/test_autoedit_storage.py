"""Built during the Meta Global AI Developer Hackathon (new work).

The storage interface: local backend, S3/R2 backend against a fake client (the founder has no bucket yet), retention sweep, key safety."""
import datetime as dt
import os
import time

import pytest

from app.studio.autoedit import storage as ST


class FakeS3:
    """Just enough of boto3's S3 client: objects in a dict, paginated listing, presigned URLs."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, dt.datetime]] = {}
        self.calls: list[str] = []

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.calls.append("upload_file")
        self.objects[key] = (open(path, "rb").read(), dt.datetime.now(dt.timezone.utc))

    def put_object(self, Bucket, Key, Body, **kw):
        self.calls.append("put_object")
        self.objects[Key] = (Body, dt.datetime.now(dt.timezone.utc))

    def download_file(self, bucket, key, path):
        if key not in self.objects:
            raise KeyError(key)
        open(path, "wb").write(self.objects[key][0])

    def get_object(self, Bucket, Key):
        import io
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.objects[Key][0])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + 2]  # tiny pages to exercise pagination
        out = {"Contents": [{"Key": k, "LastModified": self.objects[k][1]} for k in page]}
        if start + 2 < len(keys):
            out.update({"IsTruncated": True, "NextContinuationToken": str(start + 2)})
        return out

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://fake.r2/{Params['Bucket']}/{Params['Key']}?exp={ExpiresIn}"


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path):
    return ST.LocalStorage(tmp_path / "root") if request.param == "local" else ST.S3Storage("bucket", client=FakeS3())


def test_put_get_exists_delete_roundtrip(store, tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    store.put_file("uploads/u1/a1/original.mp4", f)
    store.put_bytes("edits/a1/state.json", b"{}")
    assert store.exists("uploads/u1/a1/original.mp4") and not store.exists("uploads/u1/a1/nope")
    assert store.get_bytes("edits/a1/state.json") == b"{}"
    out = store.get_file("uploads/u1/a1/original.mp4", tmp_path / "x" / "copy.bin")
    assert out.read_bytes() == b"hello"
    store.delete("edits/a1/state.json")
    assert not store.exists("edits/a1/state.json")


def test_missing_objects_raise_a_storage_error(store, tmp_path):
    with pytest.raises(ST.StorageError):
        store.get_bytes("edits/none/x")
    with pytest.raises(ST.StorageError):
        store.get_file("edits/none/x", tmp_path / "y")


def test_list_and_delete_prefix_cover_everything_under_it(store):
    for i in range(5):
        store.put_bytes(f"edits/a1/f{i}", b"x")
    store.put_bytes("edits/a2/keep", b"x")
    assert len(store.list("edits/a1/")) == 5          # more than one page on the fake
    assert store.delete_prefix("edits/a1/") == 5
    assert [k for k, _ in store.list("edits/")] == ["edits/a2/keep"]


def test_retention_sweep_deletes_only_old_objects(tmp_path):
    local = ST.LocalStorage(tmp_path / "r")
    local.put_bytes("edits/old/f", b"x")
    local.put_bytes("edits/new/f", b"x")
    old = time.time() - 8 * 86400
    os.utime(local._path("edits/old/f"), (old, old))
    assert local.sweep("edits/", 7) == 1
    assert local.exists("edits/new/f") and not local.exists("edits/old/f")
    s3 = ST.S3Storage("b", client=FakeS3())
    s3.put_bytes("edits/old/f", b"x")
    s3.client.objects["edits/old/f"] = (b"x", dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=9))
    s3.put_bytes("edits/new/f", b"x")
    assert s3.sweep("edits/", 7) == 1 and s3.exists("edits/new/f")


def test_keys_cannot_escape_the_storage_root(tmp_path):
    local = ST.LocalStorage(tmp_path / "r")
    for bad in ("../x", "/etc/passwd", "a/../../b", "a//b", "", "a b"):
        with pytest.raises(ST.StorageError):
            local.put_bytes(bad, b"x")


def test_s3_gives_a_presigned_url_and_local_does_not(tmp_path):
    s3 = ST.S3Storage("bkt", client=FakeS3(), prefix="prysm")
    s3.put_bytes("edits/a/final.mp4", b"x")
    assert "prysm/edits/a/final.mp4" in s3.client.objects.__iter__().__next__()
    assert s3.url("edits/a/final.mp4", 60).endswith("exp=60")
    assert ST.LocalStorage(tmp_path / "r").url("x") is None


def test_factory_reads_r2_variables_and_refuses_missing_ones(monkeypatch, tmp_path):
    ST.set_storage(None)
    monkeypatch.setenv("AUTO_EDIT_STORAGE", "s3")
    for k in ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("app.studio.config.get_env", lambda name: os.environ.get(name))
    with pytest.raises(ST.StorageError, match="R2_ENDPOINT"):
        ST.get_storage()
    monkeypatch.setenv("AUTO_EDIT_STORAGE", "local")
    monkeypatch.setenv("AUTO_EDIT_STORAGE_DIR", str(tmp_path / "d"))
    assert isinstance(ST.get_storage(), ST.LocalStorage)
    ST.set_storage(None)
