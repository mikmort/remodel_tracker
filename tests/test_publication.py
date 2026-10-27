import json
import os
from pathlib import Path

import pytest
from conftest import write_source

from app.project import EditError, effective_model, make_edit
from app.publication import (
    MODEL_PATH,
    document_path,
    inspect_archive,
    load_workspace,
    publish_archive,
    source_file,
)
from app.storage import (
    ConflictError,
    LocalStore,
    MissingObjectError,
    file_digest,
    json_bytes,
    native_path,
)


def test_read_only_inspection_validates_links_and_does_not_touch_source(source):
    before = {
        str(path): (path.stat().st_mtime_ns, file_digest(path))
        for path in source.rglob("*")
        if path.is_file()
    }
    snapshot, summary, files = inspect_archive(source)
    after = {
        str(path): (path.stat().st_mtime_ns, file_digest(path))
        for path in source.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert summary["documents"] == 1
    assert summary["selections"] == 2
    assert len(snapshot["documents"]) == len(files) == 1


def test_local_store_reopens_with_persistent_cas_state(store):
    state = store.get("workspace.json")
    reopened = LocalStore(store.root)
    assert reopened.get("workspace.json") == state
    with pytest.raises(ConflictError):
        reopened.put("workspace.json", b"overwrite without revision")
    reopened.put("workspace.json", b"new private revision", expected=state.etag)
    with pytest.raises(ConflictError):
        store.put("workspace.json", b"stale", expected=state.etag)
    with pytest.raises(MissingObjectError):
        store.get("not-present.json")


@pytest.mark.parametrize(
    "key", ["../outside", "/absolute", "a//b", "a/../b", "a\\b", "x?sig=anything"]
)
def test_store_rejects_noncanonical_keys(store, key):
    with pytest.raises(ValueError):
        store.get(key)


def test_repo_cannot_be_a_private_store():
    checkout = Path(__file__).resolve().parents[1]
    for path in (checkout, checkout / "private", checkout.parent):
        with pytest.raises(ValueError):
            LocalStore(path)


def test_publish_is_byte_exact_and_deduplicates_content(store, source):
    workspace, snapshot, etag = load_workspace(store)
    entry = next(iter(snapshot["documents"].values()))
    assert (
        b"".join(store.chunks(entry["key"], 0, entry["size"]))
        == source_file(source, entry["path"]).read_bytes()
    )
    assert workspace["history"][0]["action"] == "publish"
    assert etag


def test_changed_completed_output_is_not_accepted(source):
    model = source / MODEL_PATH
    model.write_bytes(model.read_bytes() + b" ")
    with pytest.raises(ValueError, match="completed publication"):
        inspect_archive(source)


@pytest.mark.parametrize(
    "url",
    [
        "../outside.pdf",
        "%2e%2e/outside.pdf",
        "C:/outside.pdf",
        "https://user:password@example.invalid/x",
        "javascript:alert(1)",
        "//example.invalid/x",
        "Sources/Example%20evidence.pdf?token=secret",
    ],
)
def test_escaping_and_unsafe_urls_are_blocked(source, url):
    model = json.loads((source / MODEL_PATH).read_bytes())
    model["selections"][0]["links"][0]["url"] = url
    write_source(source, model)
    with pytest.raises(ValueError):
        inspect_archive(source)


def test_safe_https_source_is_not_downloaded():
    assert document_path("https://example.invalid/evidence") is None


def test_missing_document_fails_without_activating_new_source(source, store):
    before = store.get("workspace.json")
    document = next((source / "Sources").iterdir())
    document.unlink()
    with pytest.raises(ValueError):
        publish_archive(source, store, "test-operator")
    assert store.get("workspace.json") == before


def test_republish_reconciles_expected_source_values_and_preserves_audit(source, store):
    workspace, snapshot, etag = load_workspace(store)
    updated = make_edit(
        workspace,
        snapshot["model"],
        "selection",
        "selection-1",
        {"finish": "Black"},
        "Owner direction",
        "test-owner",
        "2026-01-03",
    )
    store.put("workspace.json", json_bytes(updated), expected=etag)
    model = json.loads((source / MODEL_PATH).read_bytes())
    model["selections"][0]["finish"] = "Brass"
    write_source(source, model)
    before = store.get("workspace.json")
    with pytest.raises(EditError, match="Source evidence changed"):
        publish_archive(source, store, "test-operator")
    assert store.get("workspace.json") == before
    model["selections"][0]["finish"] = "Black"
    write_source(source, model)
    publish_archive(source, store, "test-operator")
    current, _snapshot, _etag = load_workspace(store)
    assert [event["action"] for event in current["history"]] == ["publish", "edit", "publish"]


def test_failed_conditional_activation_keeps_old_source(source, store, monkeypatch):
    before = store.get("workspace.json")
    original = store.put

    def conflicting_put(key, data, **kwargs):
        if key == "workspace.json":
            raise ConflictError()
        return original(key, data, **kwargs)

    monkeypatch.setattr(store, "put", conflicting_put)
    with pytest.raises(ConflictError):
        publish_archive(source, store, "test-operator")
    assert store.get("workspace.json") == before


def test_derived_purchased_door_binding_cannot_be_independently_reassigned(source, tmp_path):
    model = json.loads((source / MODEL_PATH).read_bytes())
    model["selections"][1]["room"] = "DOOR-1 - study"
    write_source(source, model)
    store = LocalStore(tmp_path / "bound-store")
    publish_archive(source, store, "test-operator")
    workspace, snapshot, _etag = load_workspace(store)
    assert snapshot["model"]["selections"][1]["doorDerived"]
    with pytest.raises(EditError, match="derived"):
        make_edit(
            workspace,
            snapshot["model"],
            "selection",
            "selection-2",
            {"room": "Elsewhere"},
            "Move",
            "owner",
            "2026-01-03",
        )
    with pytest.raises(EditError, match="purchased product"):
        make_edit(
            workspace,
            snapshot["model"],
            "door",
            "DOOR-1",
            {"lever": "Other lock"},
            "Replace",
            "owner",
            "2026-01-03",
        )
    updated = make_edit(
        workspace,
        snapshot["model"],
        "door",
        "DOOR-1",
        {"location": "Library"},
        "Room name correction",
        "owner",
        "2026-01-03",
    )
    effective = effective_model(snapshot["model"], updated["overrides"])
    assert effective["selections"][1]["room"] == "DOOR-1 - library"


@pytest.mark.skipif(os.name != "nt", reason="Windows extended paths")
def test_windows_long_source_paths_are_supported(source):
    directory = source / ("long-directory-" * 12)
    filename = directory / ("example-" * 12 + ".pdf")
    native_path(directory).mkdir()
    native_path(filename).write_bytes(b"synthetic long path")
    relative = str(filename.relative_to(source))
    assert len(str(filename)) > 260
    assert source_file(source, relative).read_bytes() == b"synthetic long path"


def test_local_upload_checks_hash_before_publishing(store, source):
    path = source_file(source, "Sources\\Example evidence.pdf")
    with pytest.raises(ValueError, match="Source changed"):
        store.upload("files/wrong-hash", path, "incorrect")
    assert not (store.root / "files" / "wrong-hash").exists()


def test_changed_source_input_requires_a_source_rebuild(source):
    from app.publication import MANIFEST_PATH

    manifest_path = source / MANIFEST_PATH
    manifest = json.loads(manifest_path.read_bytes())
    manifest["sourceInputs"] = {"Sources\\Example evidence.pdf": "stale-source-hash"}
    manifest_path.write_bytes(json_bytes(manifest))
    with pytest.raises(ValueError, match="Source evidence or metadata changed"):
        inspect_archive(source)
