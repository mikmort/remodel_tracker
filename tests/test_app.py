import base64
import copy
import hashlib
import io

import pytest
from conftest import edit_headers, gateway_headers
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.auth import Settings
from app.exports import door_pdf
from app.main import create_app
from app.publication import load_workspace
from app.storage import json_bytes


def edit(
    client,
    values,
    *,
    kind="selection",
    identifier="selection-1",
    headers=None,
    basis="Owner confirmed after reviewing source",
):
    return client.post(
        f"/api/edits/{kind}/{identifier}",
        json={"values": values, "basis": basis},
        headers=headers or edit_headers(client),
    )


def test_anonymous_and_different_owner_are_denied(client):
    client.headers.clear()
    for path in (
        "/api/project",
        "/api/history",
        "/api/exports/edits.json",
        "/api/documents/" + "0" * 32 + "/anything",
    ):
        assert client.get(path).status_code == 401
    for headers in [
        gateway_headers(email="outsider@example.invalid"),
        gateway_headers(identifier="different-id"),
        gateway_headers(provider="google", identifier="owner-id"),
    ]:
        assert client.get("/api/project", headers=headers).status_code == 403
    assert (
        client.get("/api/project", headers=gateway_headers("google", "google-owner-id")).status_code
        == 200
    )


def test_matching_email_without_enrolled_id_never_grants_access(store):
    settings = Settings("https://app.example.invalid", "owner@example.invalid", app_service=True)
    with TestClient(create_app(settings, store)) as client:
        identity = client.get("/api/identity", headers=gateway_headers())
        assert identity.json()["authorized"] is False
        assert identity.json()["id"] == "owner-id"
        assert client.get("/api/project", headers=gateway_headers()).status_code == 403


def test_forged_headers_on_a_standalone_server_are_rejected(store):
    settings = Settings("https://app.example.invalid", "owner@example.invalid", aad_id="owner-id")
    with TestClient(create_app(settings, store)) as client:
        assert client.get("/api/project", headers=gateway_headers()).status_code == 503


def test_public_shell_has_no_private_records_and_has_privacy_headers(client):
    client.headers.clear()
    response = client.get("/")
    assert response.status_code == 200
    assert "MODEL-1" not in response.text
    assert "Example faucet" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "noindex" in response.text
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/openapi.json").status_code == 404


def test_public_auth_options_have_no_owner_information_and_do_not_grant_access(client):
    from dataclasses import replace

    client.headers.clear()
    options = client.get("/auth-options")
    assert options.status_code == 200
    assert options.json() == {"microsoft": False, "google": False}
    client.app.state.settings = replace(
        client.app.state.settings, microsoft_login_enabled=True, google_login_enabled=True
    )
    assert client.get("/auth-options").json() == {"microsoft": True, "google": True}
    assert client.get("/api/project").status_code == 401


@pytest.mark.parametrize("principal", ["not base64", base64.b64encode(b"[]").decode(), "e30="])
def test_malformed_identity_is_401_not_500(client, principal):
    response = client.get("/api/project", headers={"x-ms-client-principal": principal})
    assert response.status_code == 401


def test_edit_is_persistent_audited_and_does_not_change_finances(client, store):
    before = client.get("/api/project").json()
    response = edit(client, {"selectionState": "Finalized"})
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["revision"] == before["revision"] + 1
    assert after["etag"] != before["etag"]
    assert after["model"]["selections"][0]["selectionState"] == "Finalized"
    assert after["model"]["finance"] == before["model"]["finance"]
    assert after["model"]["spending"] == before["model"]["spending"]
    workspace, snapshot, _etag = load_workspace(store)
    assert workspace["history"][-1]["before"] == {"selectionState": "Open"}
    assert workspace["history"][-1]["after"] == {"selectionState": "Finalized"}
    assert workspace["history"][-1]["actor"] == "aad:owner-id"
    assert snapshot["model"]["selections"][0]["selectionState"] == "Open"
    assert (
        client.get("/api/project").json()["model"]["selections"][0]["selectionState"] == "Finalized"
    )
    assert after["model"]["selectionReconciliation"]["after"]["Finalized"] == 2
    assert after["model"]["ownerBudget"]["unconfirmedOwnerSelections"][0]["id"] == "selection-1"


def test_stale_writes_missing_revision_and_cross_origin_are_rejected(client):
    original = client.get("/api/project").json()["etag"]
    assert (
        edit(client, {"notes": "First"}, headers=edit_headers(client, original)).status_code == 200
    )
    assert (
        edit(client, {"notes": "Second"}, headers=edit_headers(client, original)).status_code == 409
    )
    headers = edit_headers(client)
    del headers["If-Match"]
    assert edit(client, {"notes": "Second"}, headers=headers).status_code == 428
    for key, value in [
        ("Origin", "https://outsider.example.invalid"),
        ("X-Requested-With", ""),
        ("Sec-Fetch-Site", "cross-site"),
    ]:
        headers = edit_headers(client)
        headers[key] = value
        assert edit(client, {"notes": "Forged"}, headers=headers).status_code == 403
    assert len(client.get("/api/history").json()["entries"]) == 2


@pytest.mark.parametrize(
    "values",
    [
        {"purchaseState": "Purchased"},
        {"purchaser": "Owner"},
        {"paidDate": "2026-01-02"},
        {"selectionState": "Approved"},
        {"price": "12.123", "priceBasis": "Quote"},
        {"price": "-1", "priceBasis": "Quote"},
        {"price": "Infinity", "priceBasis": "Quote"},
        {"price": "NaN", "priceBasis": "Quote"},
        {"price": "1e10000", "priceBasis": "Quote"},
        {"price": "12"},
        {"notes": "x" * 10001},
        {},
    ],
)
def test_invalid_edits_are_rejected_without_history(client, values):
    before = client.get("/api/history").json()
    assert edit(client, values).status_code == 422
    assert client.get("/api/history").json() == before


def test_price_reference_requires_basis_and_never_alters_paid_totals(client):
    before = client.get("/api/project").json()["model"]
    response = edit(client, {"price": "425", "priceBasis": "Working reference before tax"})
    assert response.status_code == 200, response.text
    model = response.json()["model"]
    assert model["selections"][0]["price"] == "$425.00"
    assert model["ownerBudget"]["totals"] == before["ownerBudget"]["totals"]
    assert model["finance"] == before["finance"]
    assert edit(client, {"priceBasis": "Revised quote qualification"}).status_code == 200


def test_purchased_variant_is_blocked_and_changed_image_is_removed(client):
    assert edit(client, {"model": "OTHER"}, identifier="selection-2").status_code == 422
    result = edit(client, {"model": "OTHER"})
    assert result.status_code == 200
    model = result.json()["model"]
    assert "image" not in model["selections"][0]
    assert model["appEdits"]["warnings"]
    assert model["selectionImages"]["picturedSelections"] == 0


def test_restore_preserves_history_and_prior_override(client):
    assert edit(client, {"selectionState": "Historical"}).status_code == 200
    response = client.post(
        "/api/restore/selection/selection-1",
        json={"basis": "Restore reviewed source choice"},
        headers=edit_headers(client),
    )
    assert response.status_code == 200
    assert response.json()["model"]["selections"][0]["selectionState"] == "Open"
    assert response.json()["model"]["appEdits"]["sourceExportsCurrent"]
    entries = client.get("/api/history").json()["entries"]
    assert [entry["action"] for entry in entries] == ["publish", "edit", "restore"]
    assert entries[-1]["previousOverride"]["values"]["selectionState"] == "Historical"


def test_authenticated_document_range_and_export_safety(client):
    project = client.get("/api/project").json()
    url = project["model"]["selections"][0]["links"][0]["url"].split("#")[0]
    response = client.get(url)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert response.headers["cache-control"] == "no-store"
    for byte_range, expected in [
        ("bytes=0-3", b"%PDF"),
        ("bytes=-5", b"%%EOF"),
        ("bytes=4-", response.content[4:]),
    ]:
        partial = client.get(url, headers={"Range": byte_range})
        assert partial.status_code == 206
        assert partial.content == expected
        assert int(partial.headers["content-length"]) == len(expected)
    for invalid in ("bytes=99999-", "bytes=0-1,4-5", "bytes=-0", "bytes=5-2", "garbage"):
        assert client.get(url, headers={"Range": invalid}).status_code == 416
    assert client.get("/api/documents/not-indexed").status_code == 404
    assert edit(client, {"notes": "=SUM(1,2)", "item": '=HYPERLINK("bad")'}).status_code == 200
    csv = client.get("/api/exports/selections.csv")
    assert csv.content.startswith(b"\xef\xbb\xbf")
    assert "'=HYPERLINK" in csv.text
    assert "r2-" in csv.headers["content-disposition"]
    assert client.get("/api/exports/doors.pdf").content.startswith(b"%PDF")


def test_original_workbook_is_exact_and_is_blocked_after_door_edits(client):
    source = client.get("/api/exports/source-workbook.xlsx")
    assert source.status_code == 200
    assert source.content == b"synthetic unchanged workbook bytes"
    result = edit(client, {"finish": "Black"}, kind="door", identifier="DOOR-1")
    assert result.status_code == 200
    assert result.json()["model"]["doorHardware"]["schedule"][0]["finish"] == "Black"
    assert client.get("/api/exports/source-workbook.xlsx").status_code == 409
    current = client.get("/api/exports/doors.csv")
    assert "Black" in current.text
    text = "\n".join(
        page.extract_text()
        for page in PdfReader(io.BytesIO(client.get("/api/exports/doors.pdf").content)).pages
    )
    assert "Black" in text
    assert "not a purchase order" in text


def test_pdf_contains_every_opening_and_escaped_notes(client):
    model = client.get("/api/project").json()["model"]
    template = model["doorHardware"]["schedule"][0]
    model["doorHardware"]["schedule"] = [
        {**copy.deepcopy(template), "id": f"TEST-{index:02}", "note": "Check <fit> & finish"}
        for index in range(1, 40)
    ]
    document = PdfReader(io.BytesIO(door_pdf(model, 42)))
    assert len(document.pages) >= 1
    text = "\n".join(page.extract_text() for page in document.pages)
    assert all(f"TEST-{index:02}" in text for index in range(1, 40))
    assert "Check <fit> & finish" in text
    assert "App revision 42" in text


def test_invalid_fields_do_not_echo_private_input(client):
    response = client.post(
        "/api/edits/selection/selection-1",
        headers=edit_headers(client),
        json={"values": {"notes": 123}, "basis": "SYNTHETIC_PRIVATE_INPUT"},
    )
    assert response.status_code == 422
    assert "SYNTHETIC_PRIVATE_INPUT" not in response.text


def test_corrupt_workbook_is_explicit_error(client, store):
    workspace, snapshot, _etag = load_workspace(store)
    snapshot["model"]["doorHardware"]["excelSha256"] = hashlib.sha256(b"different").hexdigest()
    raw = store.get(workspace["snapshot"])
    store.put(workspace["snapshot"], json_bytes(snapshot), expected=raw.etag)
    assert client.get("/api/exports/source-workbook.xlsx").status_code == 503


def test_request_size_is_bounded_even_for_chunked_json(client):
    headers = {**edit_headers(client), "Content-Type": "application/json"}
    response = client.post(
        "/api/edits/selection/selection-1",
        headers=headers,
        content=iter([b'{"values":{"notes":"', b"x" * 65536, b'"},"basis":"Large"}']),
    )
    assert response.status_code == 413
    assert response.headers["cache-control"] == "no-store"
    assert len(client.get("/api/history").json()["entries"]) == 1


def test_document_links_remain_bound_to_the_source_snapshot(client, source, store):
    from app.publication import publish_archive

    before_url = client.get("/api/project").json()["model"]["selections"][0]["links"][0]["url"]
    before = client.get(before_url).content
    original = next((source / "Sources").iterdir())
    original.write_bytes(b"%PDF-1.7\nnewer synthetic evidence\n%%EOF")
    publish_archive(source, store, "test-republication")
    after_url = client.get("/api/project").json()["model"]["selections"][0]["links"][0]["url"]
    assert before_url != after_url
    assert client.get(before_url).content == before
    assert client.get(after_url).content == original.read_bytes()
