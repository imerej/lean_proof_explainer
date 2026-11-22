"""Import closures of Mathlib modules, computed from source headers.

Used to pick benchmark theorems whose files can be checked after building only a
small part of Mathlib (useful when the prebuilt Mathlib cache is unavailable).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_IMPORT = re.compile(r"^(?:(?:public|meta)\s+)*import\s+(?:all\s+)?([\w.']+)")


class ImportGraph:
    def __init__(self, packages_dir: Path):
        self.roots: dict[str, Path] = {}
        for pkg in Path(packages_dir).iterdir():
            if not pkg.is_dir():
                continue
            for top in pkg.glob("*.lean"):
                self.roots.setdefault(top.stem, pkg)

    def path(self, module: str) -> Path | None:
        pkg = self.roots.get(module.split(".")[0])
        if pkg is None:
            return None
        p = pkg / (module.replace(".", "/") + ".lean")
        return p if p.exists() else None

    @lru_cache(maxsize=None)
    def direct(self, module: str) -> tuple[str, ...]:
        p = self.path(module)
        if p is None:
            return ()
        text = re.sub(r"/-.*?-/", "", p.read_text(encoding="utf-8"), flags=re.S)
        out = []
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("--") or s in ("module", "prelude"):
                continue
            m = _IMPORT.match(s)
            if not m:
                break
            out.append(m.group(1))
        return tuple(out)

    def closure(self, module: str) -> set[str]:
        """All package modules (not Lean core) that `module` depends on, including itself."""
        seen: set[str] = set()
        stack = [module]
        while stack:
            m = stack.pop()
            if m in seen or self.path(m) is None:
                continue
            seen.add(m)
            stack.extend(self.direct(m))
        return seen


def module_of(file: str) -> str:
    return file.removesuffix(".lean").replace("/", ".")
