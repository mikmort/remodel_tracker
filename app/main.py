from __future__ import annotations

import base64
import copy
import hashlib
import itertools
import json
import logging
import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.auth import Identity, Settings, authenticated, mutation, owner
from app.exports import door_csv, door_pdf, selection_csv
from app.project import DOOR_FIELDS, SELECTION_FIELDS, EditError, effective_model, make_edit
from app.publication import load_workspace, utc_now, web_model
from app.storage import (
    ConflictError,
    MissingObjectError,
    Store,
    configured_store,
    json_bytes,
)

logger = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")


class EditBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    values: dict[str, str] = Field(min_length=1, max_length=16)
    basis: str = Field(min_length=1, max_length=4000)


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    basis: str = Field(min_length=1, max_length=4000)


async def limited_json(request: Request):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "JSON requests are required")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 65536:
            raise HTTPException(413, "Edit request exceeds 64 KiB")
        data.extend(chunk)
    try:
        return json.loads(data)
    except (ValueError, UnicodeDecodeError) as error:
        raise HTTPException(422, "Invalid JSON request") from error


async def edit_body(request: Request) -> EditBody:
    try:
        return EditBody.model_validate(await limited_json(request))
    except ValidationError as error:
        raise HTTPException(422, "Invalid request fields or values") from error


async def restore_body(request: Request) -> RestoreBody:
    try:
        return RestoreBody.model_validate(await limited_json(request))
    except ValidationError as error:
        raise HTTPException(422, "Invalid request fields or values") from error


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    application.state.settings = settings or Settings.environment()
    application.state.store = store

    def storage() -> Store:
        if application.state.store is None:
            application.state.store = configured_store()
        return application.state.store

    @application.middleware("http")
    async def privacy_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' blob:; frame-src 'self' blob:; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                ),
            }
        )
        return response

    @application.exception_handler(EditError)
    async def invalid_edit(_request, error):
        logger.warning("An owner edit failed validation")
        return JSONResponse({"detail": str(error)}, status_code=422)

    @application.exception_handler(ConflictError)
    async def conflict(_request, _error):
        logger.warning("Rejected a stale workspace write")
        return JSONResponse(
            {"detail": "The project changed. Reload and review before saving."}, status_code=409
        )

    @application.exception_handler(MissingObjectError)
    async def missing(_request, _error):
        logger.error("A required private content object is missing")
        return JSONResponse(
            {"detail": "Private content is missing or has not been published."}, status_code=503
        )

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        # FastAPI's default validation response can echo private request contents.
        return JSONResponse({"detail": "Invalid request fields or values"}, status_code=422)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.get("/auth-options")
    def auth_options():
        settings = application.state.settings
        return {
            "microsoft": settings.microsoft_login_enabled,
            "google": settings.google_login_enabled,
        }

    @application.get("/")
    def shell():
        return FileResponse(STATIC / "index.html")

    @application.get("/api/identity")
    def identity_info(identity: Identity = Depends(authenticated)):
        settings = application.state.settings
        allowed = settings.aad_id if identity.provider == "aad" else settings.google_id
        return {
            "provider": identity.provider,
            "id": identity.identifier,
            "email": identity.email,
            "authorized": bool(allowed)
            and allowed == identity.identifier
            and bool(settings.owner_email)
            and identity.email == settings.owner_email,
        }

    def payload(workspace, snapshot, etag):
        model = effective_model(snapshot["model"], workspace["overrides"])
        return {
            "revision": workspace["revision"],
            "importedAt": snapshot["importedAt"],
            "sourceModelSha256": snapshot["sourceModelSha256"],
            "model": web_model(model, snapshot["documents"], workspace["snapshot"]),
            "editable": {
                "selection": sorted(SELECTION_FIELDS),
                "door": sorted(DOOR_FIELDS),
            },
            "etag": etag,
        }

    @application.get("/api/project", dependencies=[Depends(owner)])
    def project():
        workspace, snapshot, etag = load_workspace(storage())
        return JSONResponse(payload(workspace, snapshot, etag), headers={"ETag": etag})

    @application.get("/api/history", dependencies=[Depends(owner)])
    def history():
        workspace, _snapshot, etag = load_workspace(storage())
        return JSONResponse(
            {"revision": workspace["revision"], "entries": workspace["history"]},
            headers={"ETag": etag},
        )

    @application.post("/api/edits/{kind}/{identifier}")
    def edit(
        kind: Literal["selection", "door"],
        identifier: str,
        body: Annotated[EditBody, Depends(edit_body)],
        identity: Identity = Depends(mutation),
        if_match: Annotated[str | None, Header()] = None,
    ):
        if if_match is None:
            raise HTTPException(428, "Reload the project before editing (If-Match is required)")
        workspace, snapshot, etag = load_workspace(storage())
        if etag != if_match:
            raise ConflictError()
        updated = make_edit(
            workspace,
            snapshot["model"],
            kind,
            identifier,
            body.values,
            body.basis,
            identity.actor,
            utc_now(),
        )
        new_etag = storage().put("workspace.json", json_bytes(updated), expected=etag)
        return JSONResponse(payload(updated, snapshot, new_etag), headers={"ETag": new_etag})

    @application.post("/api/restore/{kind}/{identifier}")
    def restore(
        kind: Literal["selection", "door"],
        identifier: str,
        body: Annotated[RestoreBody, Depends(restore_body)],
        identity: Identity = Depends(mutation),
        if_match: Annotated[str | None, Header()] = None,
    ):
        if if_match is None:
            raise HTTPException(428, "Reload the project before restoring source values")
        workspace, snapshot, etag = load_workspace(storage())
        if if_match != etag:
            raise ConflictError()
        if not body.basis.strip():
            raise EditError("A restoration basis is required")
        if identifier not in workspace["overrides"][kind]:
            raise EditError("This record has no owner overrides")
        updated = copy.deepcopy(workspace)
        previous = updated["overrides"][kind].pop(identifier)
        updated["revision"] += 1
        updated["history"].append(
            {
                "action": "restore",
                "kind": kind,
                "id": identifier,
                "actor": identity.actor,
                "at": utc_now(),
                "basis": body.basis.strip(),
                "previousOverride": previous,
                "revision": updated["revision"],
            }
        )
        effective_model(snapshot["model"], updated["overrides"])
        new_etag = storage().put("workspace.json", json_bytes(updated), expected=etag)
        return JSONResponse(payload(updated, snapshot, new_etag), headers={"ETag": new_etag})

    @application.get("/api/documents/{snapshot_id}/{identifier}", dependencies=[Depends(owner)])
    def document(snapshot_id: str, identifier: str, request: Request):
        if not re.fullmatch(r"[a-f0-9]{32}", snapshot_id):
            raise HTTPException(404, "Unknown source publication")
        try:
            snapshot = json.loads(storage().get(f"snapshots/{snapshot_id}.json").data)
        except MissingObjectError as error:
            raise HTTPException(404, "Unknown source publication") from error
        entry = snapshot["documents"].get(identifier)
        if entry is None:
            raise HTTPException(404, "Document not found in the publication")
        size = entry["size"]
        start, end, status = 0, size - 1, 200
        headers = {"Accept-Ranges": "bytes"}
        if byte_range := request.headers.get("range"):
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range)
            if not match or not any(match.groups()) or size == 0:
                raise HTTPException(
                    416, "Unsupported byte range", headers={"Content-Range": f"bytes */{size}"}
                )
            first, last = match.groups()
            if not first:
                suffix = int(last)
                if suffix == 0:
                    raise HTTPException(416, "Invalid suffix range")
                start = max(0, size - suffix)
            else:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            if start >= size or start > end:
                raise HTTPException(
                    416, "Range outside document", headers={"Content-Range": f"bytes */{size}"}
                )
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        length = max(0, end - start + 1)
        inline = entry["mediaType"] in {
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
        }
        disposition = "inline" if inline else "attachment"
        headers.update(
            {
                "Content-Length": str(length),
                "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(entry['name'], safe='')}",
            }
        )
        iterator = storage().chunks(entry["key"], start, length)
        first_chunk = next(iterator, b"")
        return StreamingResponse(
            itertools.chain([first_chunk], iterator),
            status_code=status,
            media_type=entry["mediaType"] if inline else "application/octet-stream",
            headers=headers,
        )

    @application.get("/api/exports/{name}", dependencies=[Depends(owner)])
    def export(name: str):
        workspace, snapshot, _etag = load_workspace(storage())
        model = effective_model(snapshot["model"], workspace["overrides"])
        generators = {
            "selections.csv": (lambda: selection_csv(model), "text/csv"),
            "doors.csv": (lambda: door_csv(model), "text/csv"),
            "doors.pdf": (lambda: door_pdf(model, workspace["revision"]), "application/pdf"),
            "edits.json": (
                lambda: json_bytes(
                    {
                        "revision": workspace["revision"],
                        "overrides": workspace["overrides"],
                        "history": workspace["history"],
                        "sourceModelSha256": snapshot["sourceModelSha256"],
                    }
                ),
                "application/json",
            ),
        }
        if name == "source-workbook.xlsx":
            if not model["appEdits"]["doorWorkbookCurrent"]:
                raise HTTPException(
                    409,
                    "Door choices changed; reconcile and regenerate the source takeoff and native pivot before downloading it as current",
                )
            hardware = snapshot["model"].get("doorHardware", {})
            if not hardware.get("excelBase64"):
                raise HTTPException(404, "The imported publication has no Excel workbook")
            content = base64.b64decode(hardware["excelBase64"], validate=True)
            if hashlib.sha256(content).hexdigest() != hardware["excelSha256"]:
                logger.error("An embedded source workbook failed its integrity check")
                raise HTTPException(503, "The source workbook failed its integrity check")
            return Response(
                content,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": 'attachment; filename="source-door-schedule-and-pivot.xlsx"'
                },
            )
        if name not in generators:
            raise HTTPException(404, "Unknown export")
        generate, media_type = generators[name]
        content = generate()
        revision = hashlib.sha256(content).hexdigest()[:12]
        stem, suffix = name.rsplit(".", 1)
        return Response(
            content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{stem}-r{workspace["revision"]}-{revision}.{suffix}"',
            },
        )

    application.mount("/static", StaticFiles(directory=STATIC), name="static")
    return application


app = create_app()
