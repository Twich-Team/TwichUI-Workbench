"""Capture an existing checked feature with the fixed offline simulator profile."""

import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--execution", required=True)
    parser.add_argument("--pin", required=True, type=Path)
    parser.add_argument(
        "--scenario", choices=["notification-appearance"], default="notification-appearance"
    )
    args = parser.parse_args()
    os.umask(0o077)
    from workbench.db import engine_for
    from workbench.simulator_capture import run

    result = run(
        engine_for(args.database.resolve()), args.execution, args.pin.resolve(), args.scenario
    )
    print(result.model_dump_json())


if __name__ == "__main__":
    main()
