"""Manual, local inventory commands. No secret values are CLI arguments."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .config import Config, ConfigurationError, local_app_data, validate_path
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
    parser = argparse.ArgumentParser(description="Read-only Gmail metadata inventory; no originals are archived.")
    commands = parser.add_subparsers(dest="command", required=True)
    validation = commands.add_parser("validate", help="Inventory at most N qualifying messages in provider order")
    validation.add_argument("--limit", type=positive, default=10)
    historical = commands.add_parser("inventory", help="Resume historical inventory from the fixed cutoff")
    for command in (validation, historical):
        command.add_argument("--restart-pagination", action="store_true", help="Restart an expired checkpoint without clearing records")
    commands.add_parser("stats", help="Read local statistics without authentication or Gmail access")
    args = parser.parse_args(argv)
    try:
        config = Config.from_environment()
        if args.command == "stats":
            print(json.dumps(statistics(config.state_path, config.mailbox), indent=2))
            return 0
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
                    result = inventory(reader, state, config.mailbox, config.cutoff_ms,
                                       args.limit if args.command == "validate" else None)
                    print(json.dumps({"run": result, "state": statistics(config.state_path, config.mailbox)}, indent=2))
                finally:
                    state.close()
        return 0
    except (ConfigurationError, ProviderError, ValueError) as exc:
        print(f"Inventory stopped: {exc}", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error):
        print("Inventory stopped: local state or credential I/O failed. Check paths, permissions and disk space; preserve existing state.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; durable work will resume on the next run.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
