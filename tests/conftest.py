import base64
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import Settings
from app.main import create_app
from app.publication import MANIFEST_PATH, MODEL_PATH, publish_archive
from app.storage import LocalStore, json_bytes


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    document = root / "Sources" / "Example evidence.pdf"
    document.parent.mkdir()
    document.write_bytes(b"%PDF-1.7\nsynthetic evidence bytes\n%%EOF")
    link = {
        "label": "Synthetic evidence",
        "url": "Sources/Example%20evidence.pdf#page=1",
        "kind": "PDF",
    }
    workbook = b"synthetic unchanged workbook bytes"
    model = {
        "compiled": "2026-01-02",
        "cutoff": "2026-01-01",
        "selections": [
            {
                "id": "selection-1",
                "category": "Lighting",
                "room": "Study",
                "item": "Lamp",
                "product": "Example lamp",
                "model": "MODEL-1",
                "finish": "Nickel",
                "size": "",
                "quantity": "1",
                "price": "$10.00",
                "priceBasis": "Working reference before tax",
                "selectionState": "Open",
                "selectionBasis": "Undecided",
                "selectionFollowUp": "Choose",
                "purchaseState": "Not purchased",
                "purchaser": "Owner",
                "history": False,
                "image": {"url": link["url"], "sha256": "synthetic-image"},
                "links": [link],
            },
            {
                "id": "selection-2",
                "category": "Plumbing",
                "room": "Bath",
                "item": "Faucet",
                "product": "Example faucet",
                "model": "MODEL-2",
                "finish": "Chrome",
                "size": "",
                "price": "$20.00",
                "priceBasis": "Source invoice",
                "selectionState": "Finalized",
                "purchaseState": "Purchased",
                "purchaser": "Owner",
                "history": False,
                "links": [link],
            },
        ],
        "doorHardware": {
            "schedule": [
                {
                    "id": "DOOR-1",
                    "floor": "Ground",
                    "location": "Study",
                    "lever": "Example lever",
                    "function": "Passage",
                    "finish": "Nickel",
                    "rosette": "Round",
                    "hinges": "Existing",
                    "note": "",
                    "confirmation": "Unconfirmed",
                    "links": [link],
                }
            ],
            "plans": [],
            "links": [link],
            "excelBase64": base64.b64encode(workbook).decode(),
            "excelSha256": hashlib.sha256(workbook).hexdigest(),
        },
        "documents": [
            {
                "title": "Synthetic evidence",
                "url": link["url"],
                "path": str(Path("Sources") / document.name),
                "type": "PDF",
                "section": "Sources",
                "date": "2026-01-01",
            }
        ],
        "finance": [
            {"id": "invoice-1", "amount": "20.00", "status": "Invoice, not proof of settlement"}
        ],
        "spending": {"coreTotals": {"documentedCost": "20.00"}},
        "ownerBudget": {"totals": {"documented": "20.00"}},
        "selectionReconciliation": {"after": {}},
        "selectionProductReview": {"after": {}},
        "selectionProcurement": {},
        "selectionImages": {},
        "sourceRegisters": [],
    }
    write_source(root, model)
    return root


def write_source(root, model):
    model_file = root / MODEL_PATH
    model_file.parent.mkdir(parents=True, exist_ok=True)
    raw = json_bytes(model)
    model_file.write_bytes(raw)
    manifest = {
        "version": 1,
        "outputs": {
            str(MODEL_PATH).replace("/", "\\"): {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        },
    }
    (root / MANIFEST_PATH).write_text(json.dumps(manifest), encoding="utf-8")


def gateway_headers(provider="aad", identifier="owner-id", email="owner@example.invalid"):
    return {
        "x-ms-client-principal": base64.b64encode(
            json_bytes(
                {
                    "auth_typ": provider,
                    "claims": [{"typ": "email", "val": email}],
                }
            )
        ).decode(),
        "x-ms-client-principal-id": identifier,
        "x-ms-client-principal-idp": provider,
        "x-ms-client-principal-name": email,
    }


@pytest.fixture
def store(tmp_path, source):
    result = LocalStore(tmp_path / "private")
    publish_archive(source, result, actor="synthetic-import")
    return result


@pytest.fixture
def client(store):
    settings = Settings(
        origin="https://app.example.invalid",
        owner_email="owner@example.invalid",
        aad_id="owner-id",
        google_id="google-owner-id",
        app_service=True,
    )
    with TestClient(create_app(settings, store)) as result:
        result.headers.update(gateway_headers())
        yield result


def edit_headers(client, etag=None):
    return {
        "If-Match": etag or client.get("/api/project").json()["etag"],
        "Origin": "https://app.example.invalid",
        "X-Requested-With": "RemodelTracker",
    }
