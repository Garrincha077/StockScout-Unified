"""Validate the all-or-none public owner configuration before CI or EOD."""
from __future__ import annotations

import argparse
import json

from stockscout_eod.public_config import validate_owner_environment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require", action="store_true", help="Require a complete production setup")
    args = parser.parse_args()
    print(json.dumps(validate_owner_environment(required=args.require), sort_keys=True))


if __name__ == "__main__":
    main()
