from __future__ import annotations

import copy
from collections import Counter
from decimal import Decimal, InvalidOperation

SELECTION_FIELDS = {
    "selectionState",
    "selectionFollowUp",
    "status",
    "price",
    "priceBasis",
    "notes",
    "designNotes",
    "room",
    "item",
    "quantity",
    "size",
    "product",
    "model",
    "finish",
}
DOOR_FIELDS = {
    "confirmation",
    "location",
    "lever",
    "function",
    "finish",
    "rosette",
    "hinges",
    "note",
}
PRODUCT_FIELDS = {"product", "model", "finish", "size"}


class EditError(ValueError):
    pass


def find_row(model: dict, kind: str, identifier: str) -> dict:
    rows = (
        model["selections"]
        if kind == "selection"
        else model.get("doorHardware", {}).get("schedule", [])
    )
    matches = [row for row in rows if row["id"] == identifier]
    if len(matches) != 1:
        raise EditError("Unknown or duplicate project record ID")
    return matches[0]


def validate_edit(row: dict, kind: str, values: dict[str, str], basis: str) -> dict[str, str]:
    fields = SELECTION_FIELDS if kind == "selection" else DOOR_FIELDS
    if not basis.strip() or len(basis) > 4000:
        raise EditError("A decision or evidence basis is required (maximum 4,000 characters)")
    if not values or set(values) - fields:
        raise EditError("Unsupported or empty edit; purchase and payment evidence are not editable")
    if any(not isinstance(value, str) or len(value) > 10000 for value in values.values()):
        raise EditError("Field values must be text of at most 10,000 characters")
    values = dict(values)
    if kind == "selection":
        if "selectionState" in values and values["selectionState"] not in {
            "Finalized",
            "Open",
            "Historical",
        }:
            raise EditError("Selection state must be Finalized, Open, or Historical")
        if row.get("purchaseState") == "Purchased" and any(
            key in values and values[key] != row.get(key, "") for key in PRODUCT_FIELDS
        ):
            raise EditError(
                "A purchased variant needs source-order reconciliation before changing it"
            )
        if "price" in values:
            if not values.get("priceBasis", "").strip():
                raise EditError(
                    "Price changes require an explicit price basis, not payment evidence"
                )
            if values["price"]:
                try:
                    amount = Decimal(values["price"].replace("$", "").replace(",", ""))
                    if (
                        not amount.is_finite()
                        or amount < 0
                        or amount > Decimal("999999999.99")
                        or amount != amount.quantize(Decimal("0.01"))
                    ):
                        raise EditError(
                            "Price must be a nonnegative amount with at most two decimals"
                        )
                except InvalidOperation as error:
                    raise EditError("Price is not a valid monetary amount") from error
                values["price"] = f"${amount:,.2f}"
        if "room" in values and row.get("doorDerived"):
            raise EditError(
                "This location is derived from door metadata; reconcile that source first"
            )
    elif "confirmation" in values and values["confirmation"] not in {"Confirmed", "Unconfirmed"}:
        raise EditError("Door confirmation must be Confirmed or Unconfirmed")
    return values


def effective_model(source: dict, overrides: dict) -> dict:
    model = copy.deepcopy(source)
    changed_doors, warnings = [], []
    for kind in ("selection", "door"):
        for identifier, entry in overrides[kind].items():
            row = find_row(model, kind, identifier)
            for key, expected in entry["expected"].items():
                if row.get(key, "") not in (expected, entry["values"][key]):
                    raise EditError(
                        "Source evidence changed beneath a saved edit; reconcile before publishing"
                    )
            values = validate_edit(row, kind, entry["values"], entry["basis"])
            signature_changed = any(
                key in values and values[key] != row.get(key, "") for key in PRODUCT_FIELDS
            )
            row.update(values)
            row["ownerEdit"] = {"at": entry["at"], "basis": entry["basis"]}
            if kind == "selection":
                row["history"] = row.get("selectionState") == "Historical"
                if "selectionState" in values:
                    row["selectionBasis"] = entry["basis"]
                    row["selectionFollowUp"] = values.get(
                        "selectionFollowUp",
                        entry["basis"] if values["selectionState"] == "Open" else "",
                    )
                    row["status"] = values.get(
                        "status", "Owner direction: " + values["selectionState"]
                    )
                if signature_changed and row.pop("image", None):
                    warnings.append(f"{identifier}: verify a new product image before reusing one.")
            else:
                changed_doors.append(identifier)
    for row in model["selections"]:
        if row.get("doorDerived"):
            door = find_row(model, "door", row["doorId"])
            if row.get("purchaseState") == "Purchased":
                changed = overrides["door"].get(door["id"], {}).get("values", {})
                if {"lever", "function", "finish"} & changed.keys():
                    raise EditError(
                        "This door is bound to an existing purchased product; reconcile its source order before changing model, function, or finish"
                    )
            row["room"] = door["id"] + " - " + door["location"].lower()
    # Source financial records and reviewed totals are deliberately unchanged.
    selections = model["selections"]
    counts = dict(Counter(row.get("selectionState", "Open") for row in selections))
    for key in ("selectionReconciliation", "selectionProductReview"):
        if key in model:
            model[key]["after"] = counts
    if "selectionProcurement" in model:
        model["selectionProcurement"]["counts"] = dict(
            Counter(row.get("purchaseState", "Not purchased") for row in selections)
        )
        model["selectionProcurement"]["purchaserCounts"] = dict(
            Counter(row.get("purchaser", "To confirm") for row in selections)
        )
    if "ownerBudget" in model:
        model["ownerBudget"]["unconfirmedOwnerSelections"] = [
            {key: row.get(key, "") for key in ("id", "room", "item", "product", "links")}
            for row in selections
            if row.get("purchaser") == "Owner"
            and row.get("purchaseState") == "Not purchased"
            and not row.get("history")
        ]
    if "selectionImages" in model:
        images = [row["image"] for row in selections if row.get("image")]
        model["selectionImages"]["picturedSelections"] = len(images)
        model["selectionImages"]["unpicturedSelections"] = len(selections) - len(images)
    model["appEdits"] = {
        "selectionIds": sorted(overrides["selection"]),
        "doorIds": changed_doors,
        "warnings": warnings,
        "sourceExportsCurrent": not any(overrides[kind] for kind in overrides),
        "doorWorkbookCurrent": not changed_doors,
        "policy": "Owner choices are separate from orders, invoices, payments and source documents.",
    }
    return model


def make_edit(
    workspace: dict,
    source: dict,
    kind: str,
    identifier: str,
    values: dict[str, str],
    basis: str,
    actor: str,
    at: str,
) -> dict:
    from app.publication import utc_now

    if kind not in {"selection", "door"}:
        raise EditError("Unknown editable record type")
    model = effective_model(source, workspace["overrides"])
    row = find_row(model, kind, identifier)
    validated = validate_edit(row, kind, values, basis)
    values = {key: value for key, value in validated.items() if value != row.get(key, "")}
    if not values:
        raise EditError("No fields changed")
    if "price" in values:
        values["priceBasis"] = validated["priceBasis"]
    updated = copy.deepcopy(workspace)
    entry = updated["overrides"][kind].setdefault(
        identifier, {"expected": {}, "values": {}, "basis": "", "at": ""}
    )
    original = find_row(source, kind, identifier)
    for key in values:
        entry["expected"].setdefault(key, original.get(key, ""))
    entry["values"].update(values)
    entry.update(basis=basis.strip(), at=at)
    updated["revision"] += 1
    updated["history"].append(
        {
            "action": "edit",
            "kind": kind,
            "id": identifier,
            "actor": actor,
            "at": at or utc_now(),
            "basis": basis.strip(),
            "revision": updated["revision"],
            "before": {key: row.get(key, "") for key in values},
            "after": values,
        }
    )
    effective_model(source, updated["overrides"])
    return updated
