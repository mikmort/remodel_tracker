from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.publication import inspect_archive, publish_archive
from app.storage import BlobStore, ConflictError, LocalStore


def main():
    parser = argparse.ArgumentParser(description="Inspect or publish a private source archive.")
    parser.add_argument("command", choices=["inspect", "publish"])
    parser.add_argument("--source", type=Path, required=True)
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("--store", type=Path, help="Private directory OUTSIDE this checkout")
    destination.add_argument("--account-url", help="Azure storage HTTPS account origin")
    parser.add_argument("--container", default="project")
    args = parser.parse_args()
    if args.command == "inspect":
        if args.store or args.account_url:
            parser.error("inspect is read-only and does not take a destination")
        _snapshot, summary, _files = inspect_archive(args.source)
    else:
        if not args.store and not args.account_url:
            parser.error("publish requires an explicitly selected private destination")
        if args.store:
            source, target = args.source.resolve(), args.store.resolve()
            if target.is_relative_to(source) or source.is_relative_to(target):
                parser.error("Private app storage and the source archive must not overlap")
            store = LocalStore(target)
        else:
            from azure.identity import AzureCliCredential

            store = BlobStore(args.account_url, args.container, credential=AzureCliCredential())
        summary = publish_archive(args.source, store, actor="operator-import")
    print(json.dumps({"action": args.command, **summary}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, ConflictError) as error:
        # Paths and source content can be sensitive; do not dump them into deployment logs.
        reason = (
            "Check access to the private source and destination."
            if isinstance(error, OSError)
            else str(error)
        )
        print(
            f"Private publication failed ({type(error).__name__}): {reason} No new publication activated.",
            file=sys.stderr,
        )
        raise SystemExit(1) from error
