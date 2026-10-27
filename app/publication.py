from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import uuid
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlsplit

from app.storage import (
    MissingObjectError,
    Store,
    file_digest,
    file_identity,
    json_bytes,
    native_path,
)

MODEL_PATH = Path("99_Archive Administration") / "Project Portal Data.json"
MANIFEST_PATH = Path("99_Archive Administration") / "Project Publication Manifest.json"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def source_file(root: Path, relative: str) -> Path:
    windows = PureWindowsPath(relative)
    if windows.drive or windows.is_absolute() or ".." in windows.parts:
        raise ValueError("A source link escapes the private archive")
    path = root.joinpath(*windows.parts).resolve()
    if not path.is_relative_to(root.resolve()) or not native_path(path).is_file():
        raise ValueError("A referenced source is missing or outside the private archive")
    return native_path(path)


def local_urls(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "url" and isinstance(item, str):
                yield item
            else:
                yield from local_urls(item)
    elif isinstance(value, list):
        for item in value:
            yield from local_urls(item)


def document_path(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"https", "mailto"} or parsed.username or parsed.password:
            raise ValueError("Unsupported or credential-bearing source URL")
        return None
    if not parsed.path:
        return None
    if parsed.query:
        raise ValueError("Local source URLs cannot have query strings")
    return str(PureWindowsPath(unquote(parsed.path)))


def inspect_archive(root: Path) -> tuple[dict, dict, dict[str, Path]]:
    root = root.resolve()
    model_path = source_file(root, str(MODEL_PATH))
    model_raw = model_path.read_bytes()
    model = json.loads(model_raw)
    if not isinstance(model, dict) or not isinstance(model.get("selections"), list):
        raise ValueError("Expected a published project model, not an arbitrary JSON file")
    publication_path = source_file(root, str(MANIFEST_PATH))
    publication_raw = publication_path.read_bytes()
    publication = json.loads(publication_raw)
    if publication.get("version") != 1:
        raise ValueError("Unsupported source publication manifest")
    model_relative = str(MODEL_PATH).replace("/", "\\")
    declared = publication["outputs"].get(model_relative)
    if not declared or hashlib.sha256(model_raw).hexdigest() != declared["sha256"]:
        raise ValueError("Project model does not match its completed publication")
    for relative, expected in publication["outputs"].items():
        path = source_file(root, relative)
        if path.stat().st_size != expected["bytes"] or file_digest(path) != expected["sha256"]:
            raise ValueError("A maintained output differs from the completed publication")
    source_inputs = {
        **publication.get("sourceInputs", {}),
        **publication.get("metadataAndCode", {}),
    }
    for relative, expected_digest in source_inputs.items():
        if file_digest(source_file(root, relative)) != expected_digest:
            raise ValueError(
                "Source evidence or metadata changed after publication; rebuild the source handbook before importing"
            )
    forecast_keys = [key for key in model if key.endswith("Forecast")]
    if len(forecast_keys) > 1:
        raise ValueError("Multiple forecast sections require an explicit source mapping")
    if forecast_keys:
        model["contractorForecast"] = model.pop(forecast_keys[0])
    door_ids = {row["id"] for row in model.get("doorHardware", {}).get("schedule", [])}
    for row in model["selections"]:
        bindings = [
            identifier
            for identifier in door_ids
            if row.get("room", "").startswith(identifier + " - ")
        ]
        if len(bindings) > 1:
            raise ValueError("Ambiguous selection-to-door source relationship")
        if bindings:
            row.update(doorDerived=True, doorId=bindings[0])
    documents, files = {}, {}
    for relative in sorted({path for url in local_urls(model) if (path := document_path(url))}):
        path = source_file(root, relative)
        identifier = hashlib.sha256(relative.encode()).hexdigest()
        digest, size = file_identity(path)
        documents[identifier] = {
            "path": relative,
            "name": path.name,
            "size": size,
            "sha256": digest,
            "key": "files/" + digest,
            "mediaType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        }
        files[identifier] = path
    if publication_path.read_bytes() != publication_raw or model_path.read_bytes() != model_raw:
        raise ValueError(
            "The source publication changed during inspection; retry after its build completes"
        )
    snapshot = {
        "schemaVersion": 1,
        "importedAt": utc_now(),
        "sourceModelSha256": hashlib.sha256(model_raw).hexdigest(),
        "model": model,
        "documents": documents,
    }
    summary = {
        "documents": len(documents),
        "uniqueContentObjects": len({item["sha256"] for item in documents.values()}),
        "documentBytes": sum(item["size"] for item in documents.values()),
        "selections": len(model["selections"]),
        "doors": len(model.get("doorHardware", {}).get("schedule", [])),
        "verifiedPublicationOutputs": len(publication["outputs"]),
        "verifiedPublicationInputs": len(source_inputs),
    }
    return snapshot, summary, files


def publish_archive(root: Path, store: Store, actor: str) -> dict:
    from app.project import effective_model

    try:
        current = store.get("workspace.json")
    except MissingObjectError:
        current = None
    snapshot, summary, files = inspect_archive(root)
    previous = json.loads(current.data) if current else None
    workspace = (
        copy.deepcopy(previous)
        if previous
        else {
            "schemaVersion": 1,
            "revision": 0,
            "overrides": {"selection": {}, "door": {}},
            "history": [],
        }
    )
    # Reconciliation must still be valid before publishing a newer source snapshot.
    effective_model(snapshot["model"], workspace["overrides"])
    for identifier, path in files.items():
        entry = snapshot["documents"][identifier]
        store.upload(entry["key"], path, entry["sha256"])
    key = f"snapshots/{uuid.uuid4().hex}.json"
    store.put(key, json_bytes(snapshot))
    workspace["snapshot"] = key
    workspace["revision"] += 1
    workspace["history"].append(
        {
            "action": "publish",
            "at": utc_now(),
            "actor": actor,
            "revision": workspace["revision"],
            "sourceModelSha256": snapshot["sourceModelSha256"],
        }
    )
    # One conditional commit activates the source, edits, and history together.
    store.put("workspace.json", json_bytes(workspace), expected=current.etag if current else None)
    return summary


def load_workspace(store: Store) -> tuple[dict, dict, str]:
    state = store.get("workspace.json")
    workspace = json.loads(state.data)
    if workspace.get("schemaVersion") != 1:
        raise ValueError("Unsupported private workspace schema")
    snapshot = json.loads(store.get(workspace["snapshot"]).data)
    if snapshot.get("schemaVersion") != 1:
        raise ValueError("Unsupported private snapshot schema")
    return workspace, snapshot, state.etag


def web_model(model: dict, documents: dict, snapshot_key: str) -> dict:
    by_path = {item["path"]: identifier for identifier, item in documents.items()}
    snapshot_id = Path(snapshot_key).stem

    def rewrite(value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                # Exports are served separately, not duplicated into JSON or browser storage.
                if key in {"pdfBase64", "excelBase64", "download"}:
                    continue
                if key == "url" and isinstance(item, str):
                    path = document_path(item)
                    if path is not None:
                        if path not in by_path:
                            raise ValueError("The model references an unpublished private document")
                        fragment = urlsplit(item).fragment
                        item = f"/api/documents/{snapshot_id}/{by_path[path]}" + (
                            "#" + fragment if fragment else ""
                        )
                else:
                    item = rewrite(item)
                result[key] = item
            return result
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    return rewrite(model)
