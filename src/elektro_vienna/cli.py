"""Manual inventory and source archive commands. No secret values are CLI arguments."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .config import Config, ConfigurationError, local_app_data, validate_path
from .archive import archive
from .archive_store import SourceArchive
from .gmail import GmailReader, authenticate
from .inventory import inventory, scan_key
from .models import ProviderError
from .state import SQLiteState, single_operator, statistics


def positive(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Gmail inventory and immutable source archiving.")
    commands = parser.add_subparsers(dest="command", required=True)
    validation = commands.add_parser("validate", help="Inventory at most N qualifying messages in provider order")
    validation.add_argument("--limit", type=positive, default=10)
    historical = commands.add_parser("inventory", help="Resume historical inventory from the fixed cutoff")
    for command in (validation, historical):
        command.add_argument("--restart-pagination", action="store_true", help="Restart an expired checkpoint without clearing records")
    commands.add_parser("stats", help="Read local statistics without authentication or Gmail access")
    archival = commands.add_parser("archive", help="Archive originals from existing Phase 1 inventory")
    archival.add_argument("--limit", type=positive, help="Process at most N inventoried messages, incomplete first")
    pilot = commands.add_parser("pilot", help="Reconstruct up to 30 Airtable anchor cases from local archived sources")
    pilot.add_argument("--snapshot", required=True, type=Path, help="Captured Airtable JSON in private LocalAppData storage")
    pilot.add_argument("--limit", type=positive, default=25)
    pilot.add_argument("--review", type=Path, help="Attributable accept/reject decisions for the same baseline run")
    pilot.add_argument("--dry-run", action="store_true", help="Read and evaluate sources without publishing outputs")
    review_render = commands.add_parser("review-render", help="Render one curated offline case view from an existing pilot")
    review_render.add_argument("--baseline", required=True)
    review_render.add_argument("--case-id", required=True)
    review_render.add_argument("--presentation", required=True, type=Path, help="Private source-cited curation JSON")
    review_import = commands.add_parser("review-import", help="Preserve a browser-exported evaluation without changing candidates")
    review_import.add_argument("--view-id", required=True)
    review_import.add_argument("--decisions", required=True, type=Path)
    documents = commands.add_parser("review-documents", help="Preserve and compare at most ten user-supplied PDFs")
    documents.add_argument("--manifest", required=True, type=Path)
    serving = commands.add_parser("review-serve", help="Local automatic review saving for one immutable view")
    serving.add_argument("--view-id")
    serving.add_argument("--port", type=int, default=8765)
    serving.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "review-documents":
            from .review_ui import ReviewStore
            from .review_documents import publish_documents
            manifest = validate_path(args.manifest, local_app_data())
            print(json.dumps(publish_documents(ReviewStore(),json.loads(manifest.read_bytes())),indent=2))
            return 0
        if args.command == "review-serve":
            from .review_ui import ReviewStore
            from .review_local import make_server
            import webbrowser
            view_id = args.view_id
            if not view_id:
                settings = validate_path(local_app_data()/"ElektroViennaKnowledge/review/launch.json", local_app_data())
                view_id = json.loads(settings.read_bytes())["view_id"]
            lock = validate_path(local_app_data()/"ElektroViennaKnowledge/review/service.lock",local_app_data())
            lock.parent.mkdir(parents=True,exist_ok=True)
            with single_operator(lock):
                with make_server(ReviewStore(),view_id,args.port) as server:
                    print(json.dumps({"url":server.review_url,"view_id":view_id}),flush=True)
                    if args.open_browser:webbrowser.open(server.review_url)
                    server.serve_forever()
            return 0
        if args.command in ("review-render", "review-import"):
            from .review_ui import ReviewStore, publish_view, import_review
            if args.command == "review-render":
                path = validate_path(args.presentation, local_app_data())
                result = publish_view(ReviewStore(), args.baseline, args.case_id, json.loads(path.read_bytes()))
            else:
                path = validate_path(args.decisions, local_app_data())
                result = import_review(ReviewStore(), args.view_id, path.read_bytes())
            print(json.dumps(result, indent=2))
            return 0
        config = Config.from_environment()
        if args.command == "pilot":
            from .pilot import reconstruct
            snapshot = validate_path(args.snapshot, local_app_data())
            review = validate_path(args.review, local_app_data()) if args.review else None
            result = reconstruct(snapshot, config.state_path, config.mailbox, limit=args.limit,
                                 review_path=review, dry_run=args.dry_run)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "stats":
            print(json.dumps(statistics(config.state_path, config.mailbox), indent=2))
            return 0
        store = None
        if args.command == "archive":
            if not config.state_path.is_file():
                raise ProviderError("archive_inventory_missing")
            store = SourceArchive()
        lock = validate_path(Path(str(config.state_path) + ".lock"), local_app_data())
        with authenticate(config) as session:
            reader = GmailReader(session)
            if reader.mailbox() != config.mailbox.casefold():
                raise ProviderError("mailbox_identity_mismatch")
            with single_operator(lock):
                state = SQLiteState(config.state_path)
                try:
                    if getattr(args, "restart_pagination", False):
                        mode = "validation" if args.command == "validate" else "historical"
                        state.restart_checkpoint(scan_key(config.mailbox, config.cutoff_ms, mode))
                    if args.command == "archive":
                        result = archive(reader, state, store, config.mailbox, config.cutoff_ms, args.limit)
                    else:
                        result = inventory(reader, state, config.mailbox, config.cutoff_ms,
                                           args.limit if args.command == "validate" else None)
                    print(json.dumps({"run": result, "state": statistics(config.state_path, config.mailbox)}, indent=2))
                finally:
                    state.close()
        return 0
    except (ConfigurationError, ProviderError, ValueError) as exc:
        label = "Review" if args.command.startswith("review-") else {"archive": "Archive", "pilot": "Pilot"}.get(args.command, "Inventory")
        print(f"{label} stopped: {exc}", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error):
        label = "Review" if args.command.startswith("review-") else {"archive": "Archive", "pilot": "Pilot"}.get(args.command, "Inventory")
        print(f"{label} stopped: local I/O failed. Check paths, permissions and disk space; preserve existing state.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; durable work will resume on the next run.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
