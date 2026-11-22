"""Print the Mathlib modules a benchmark needs, for `lake build $(python -m lpe.modules bench.toml)`."""

import sys
import tomllib
from pathlib import Path

from .imports import module_of


def main() -> None:
    mods = []
    for path in sys.argv[1:]:
        for entry in tomllib.loads(Path(path).read_text(encoding="utf-8"))["theorem"]:
            mods.append(module_of(entry["file"]))
    print(" ".join(dict.fromkeys(mods)))


if __name__ == "__main__":
    main()
