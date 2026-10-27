from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ConflictError(Exception):
    """The stored revision changed before this write."""


class MissingObjectError(Exception):
    """The requested private object does not exist."""


@dataclass(frozen=True)
class Stored:
    data: bytes
    etag: str


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def native_path(path: Path) -> Path:
    value = str(path.absolute())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


def checked_key(key: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9._/-]+", key) or any(
        part in {"", ".", ".."} for part in key.split("/")
    ):
        raise ValueError("Invalid private object key")
    return key


class Store(Protocol):
    def get(self, key: str) -> Stored: ...

    def put(self, key: str, data: bytes, *, expected: str | None = None) -> str:
        """Create when expected is None; otherwise replace exactly that revision."""
        ...

    def upload(self, key: str, path: Path, sha256: str) -> None: ...

    def chunks(self, key: str, offset: int, length: int) -> Iterator[bytes]: ...


class LocalStore:
    """External private files plus SQLite compare-and-swap for the workspace record."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        checkout = Path(__file__).resolve().parents[1]
        if self.root.is_relative_to(checkout) or checkout.is_relative_to(self.root):
            raise ValueError("Private storage must be outside, and not contain, the checkout")
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "records.sqlite"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS objects "
                "(key TEXT PRIMARY KEY, payload BLOB NOT NULL, etag TEXT NOT NULL)"
            )

    def get(self, key: str) -> Stored:
        checked_key(key)
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT payload, etag FROM objects WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            raise MissingObjectError(key)
        return Stored(bytes(row[0]), row[1])

    def put(self, key: str, data: bytes, *, expected: str | None = None) -> str:
        checked_key(key)
        etag = '"' + hashlib.sha256(data).hexdigest() + '"'
        with sqlite3.connect(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT etag FROM objects WHERE key = ?", (key,)).fetchone()
            if (expected is None and row is not None) or (
                expected is not None and (row is None or row[0] != expected)
            ):
                raise ConflictError("The workspace has changed; reload before saving")
            connection.execute(
                "INSERT INTO objects VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, etag=excluded.etag",
                (key, data, etag),
            )
        return etag

    def upload(self, key: str, path: Path, sha256: str) -> None:
        target = self.root.joinpath(*checked_key(key).split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if file_digest(target) != sha256:
                raise ValueError("An existing private content object has changed")
            return
        descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
        try:
            digest = hashlib.sha256()
            with os.fdopen(descriptor, "wb") as output, path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != sha256:
                raise ValueError("Source changed during upload; publication was not activated")
            # Identical concurrent content-addressed uploads are harmless.
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def chunks(self, key: str, offset: int, length: int) -> Iterator[bytes]:
        target = self.root.joinpath(*checked_key(key).split("/"))
        try:
            stream = target.open("rb")
        except FileNotFoundError as error:
            raise MissingObjectError(key) from error
        with stream:
            stream.seek(offset)
            remaining = length
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise OSError("Private content is truncated")
                remaining -= len(chunk)
                yield chunk


class BlobStore:
    def __init__(self, account_url: str, container: str, credential=None):
        from urllib.parse import urlsplit

        from azure.identity import ManagedIdentityCredential
        from azure.storage.blob import ContainerClient

        address = urlsplit(account_url)
        if (
            address.scheme != "https"
            or not address.hostname
            or address.username
            or address.password
            or address.query
            or address.fragment
            or address.path not in {"", "/"}
        ):
            raise ValueError("Use an HTTPS storage account origin, not a key or SAS URL")
        self.container = ContainerClient(
            account_url,
            container,
            credential=credential if credential is not None else ManagedIdentityCredential(),
        )

    def get(self, key: str) -> Stored:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            download = self.container.get_blob_client(checked_key(key)).download_blob()
            return Stored(download.readall(), download.properties.etag)
        except ResourceNotFoundError as error:
            raise MissingObjectError(key) from error

    def put(self, key: str, data: bytes, *, expected: str | None = None) -> str:
        from azure.core import MatchConditions
        from azure.core.exceptions import (
            ResourceExistsError,
            ResourceModifiedError,
            ResourceNotFoundError,
        )
        from azure.storage.blob import ContentSettings

        arguments = {"overwrite": False}
        if expected is not None:
            arguments = {
                "overwrite": True,
                "etag": expected,
                "match_condition": MatchConditions.IfNotModified,
            }
        try:
            result = self.container.get_blob_client(checked_key(key)).upload_blob(
                data,
                content_settings=ContentSettings(content_type="application/json"),
                **arguments,
            )
            return result["etag"]
        except (ResourceExistsError, ResourceModifiedError) as error:
            raise ConflictError("The workspace has changed; reload before saving") from error
        except ResourceNotFoundError as error:
            if expected is not None:
                raise ConflictError("The expected workspace revision no longer exists") from error
            raise MissingObjectError(key) from error

    def upload(self, key: str, path: Path, sha256: str) -> None:
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import ContentSettings

        blob = self.container.get_blob_client(checked_key(key))
        try:
            with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as stream:
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        stream.write(chunk)
                if digest.hexdigest() != sha256:
                    raise ValueError("Source changed during upload; publication was not activated")
                stream.seek(0)
                blob.upload_blob(
                    stream,
                    overwrite=False,
                    metadata={"sha256": sha256},
                    content_settings=ContentSettings(content_type="application/octet-stream"),
                )
        except ResourceExistsError:
            properties = blob.get_blob_properties()
            if (
                properties.metadata.get("sha256") != sha256
                or properties.size != path.stat().st_size
            ):
                raise ValueError(
                    "Existing private content does not match the publication"
                ) from None

    def chunks(self, key: str, offset: int, length: int) -> Iterator[bytes]:
        from azure.core.exceptions import ResourceNotFoundError

        if length == 0:
            return
        try:
            yield from (
                self.container.get_blob_client(checked_key(key))
                .download_blob(offset=offset, length=length)
                .chunks()
            )
        except ResourceNotFoundError as error:
            raise MissingObjectError(key) from error


def file_digest(path: Path) -> str:
    with native_path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_identity(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with native_path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def configured_store() -> Store:
    if path := os.environ.get("PRIVATE_STORE"):
        return LocalStore(Path(path))
    return BlobStore(
        os.environ["STORAGE_ACCOUNT_URL"], os.environ.get("STORAGE_CONTAINER", "project")
    )
