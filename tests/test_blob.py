from types import SimpleNamespace

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError

from app.storage import BlobStore, ConflictError, MissingObjectError, file_digest


class FakeContainer:
    def __init__(self, blob):
        self.blob = blob

    def get_blob_client(self, _key):
        return self.blob


def store_with(blob):
    store = BlobStore.__new__(BlobStore)
    store.container = FakeContainer(blob)
    return store


def test_blob_state_replace_uses_azure_conditional_etag():
    recorded = {}

    def upload(data, **kwargs):
        recorded.update(kwargs)
        return {"etag": '"new-etag"'}

    store = store_with(SimpleNamespace(upload_blob=upload))
    assert store.put("workspace.json", b"{}", expected='"old-etag"') == '"new-etag"'
    assert recorded["etag"] == '"old-etag"'
    assert recorded["match_condition"] is MatchConditions.IfNotModified
    store.put("snapshots/new.json", b"{}")
    assert recorded["overwrite"] is False


@pytest.mark.parametrize("exception", [ResourceExistsError, ResourceModifiedError])
def test_blob_write_conflicts_are_not_success_shaped(exception):
    def upload(*args, **kwargs):
        raise exception("Synthetic race")

    with pytest.raises(ConflictError):
        store_with(SimpleNamespace(upload_blob=upload)).put("workspace.json", b"{}")


def test_blob_missing_state_is_explicit():
    def download():
        raise ResourceNotFoundError("Synthetic missing")

    with pytest.raises(MissingObjectError):
        store_with(SimpleNamespace(download_blob=download)).get("workspace.json")


def test_deleted_blob_revision_is_a_write_conflict():
    def upload(*args, **kwargs):
        raise ResourceNotFoundError("Synthetic deletion")

    store = store_with(SimpleNamespace(upload_blob=upload))
    with pytest.raises(ConflictError):
        store.put("workspace.json", b"{}", expected='"deleted-revision"')
    with pytest.raises(MissingObjectError):
        store.put("snapshots/new.json", b"{}")


def test_missing_document_stream_is_explicit():
    def download(**kwargs):
        raise ResourceNotFoundError("Synthetic missing document")

    store = store_with(SimpleNamespace(download_blob=download))
    with pytest.raises(MissingObjectError):
        next(store.chunks("files/synthetic", 0, 5))


def test_blob_upload_snapshots_and_hashes_source_before_sending(tmp_path):
    source = tmp_path / "synthetic.pdf"
    source.write_bytes(b"exact original bytes")
    expected = file_digest(source)
    uploaded = []

    def upload(stream, **kwargs):
        source.write_bytes(b"concurrent later edit")
        uploaded.append(stream.read())
        assert kwargs["metadata"]["sha256"] == expected

    store_with(SimpleNamespace(upload_blob=upload)).upload("files/synthetic", source, expected)
    assert uploaded == [b"exact original bytes"]
    with pytest.raises(ValueError, match="Source changed"):
        store_with(SimpleNamespace(upload_blob=upload)).upload("files/changed", source, expected)


def test_blob_existing_content_metadata_is_checked(tmp_path):
    source = tmp_path / "synthetic.pdf"
    source.write_bytes(b"bytes")

    def upload(*args, **kwargs):
        raise ResourceExistsError("Already uploaded")

    blob = SimpleNamespace(
        upload_blob=upload,
        get_blob_properties=lambda: SimpleNamespace(metadata={"sha256": "wrong"}, size=5),
    )
    with pytest.raises(ValueError, match="does not match"):
        store_with(blob).upload("files/synthetic", source, file_digest(source))
