#!/usr/bin/env bash
# Install elan + the pinned Lean toolchain and build the Mathlib modules a benchmark needs.
#
# Works on machines where releases.lean-lang.org and cache.mathlib.org are unreachable but
# GitHub is not: the toolchain is fetched from the GitHub release assets and linked into elan,
# and instead of `lake exe cache get` only the import closure of the benchmark is compiled.
#
# Usage:  scripts/setup_lean.sh [benchmark.toml ...]     (default: benchmarks/pilot.toml)
# Env:    SKIP_BUILD=1   stop after `lake update` (toolchain + Mathlib sources only)
#         TRY_CACHE=1    also try `lake exe cache get` before building
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LEAN_DIR="$ROOT/lean"
BENCHES=("$@")
[ ${#BENCHES[@]} -eq 0 ] && BENCHES=("$ROOT/benchmarks/pilot.toml")

log() { printf '[setup_lean] %s\n' "$*" >&2; }

# ---------------------------------------------------------------- elan
export PATH="$HOME/.elan/bin:$PATH"
if ! command -v elan >/dev/null 2>&1; then
  log "installing elan"
  curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -o /tmp/elan-init.sh
  sh /tmp/elan-init.sh -y --default-toolchain none
fi

# ---------------------------------------------------------------- toolchain
TOOLCHAIN="$(tr -d '[:space:]' < "$LEAN_DIR/lean-toolchain")"   # e.g. leanprover/lean4:v4.36.0
VERSION="${TOOLCHAIN##*:}"                                      # v4.36.0
NAME="lean-${VERSION#v}-linux"
DEST="$HOME/.lean-toolchains/$NAME"
if ! elan toolchain list 2>/dev/null | grep -q "^${TOOLCHAIN}"; then
  if [ ! -x "$DEST/bin/lean" ]; then
    log "downloading $NAME from GitHub releases"
    mkdir -p "$HOME/.lean-toolchains"
    ARCHIVE="/tmp/$NAME.tar.zst"
    curl -sSfL "https://github.com/leanprover/lean4/releases/download/$VERSION/$NAME.tar.zst" -o "$ARCHIVE"
    python3 -c 'import zstandard' 2>/dev/null || pip install -q --break-system-packages zstandard
    python3 - "$ARCHIVE" "$HOME/.lean-toolchains" <<'PY'
import sys, tarfile, zstandard
src, dest = sys.argv[1], sys.argv[2]
with open(src, "rb") as fh, zstandard.ZstdDecompressor().stream_reader(fh) as reader:
    with tarfile.open(fileobj=reader, mode="r|") as tar:
        tar.extractall(dest, filter="tar")
PY
  fi
  log "linking $TOOLCHAIN -> $DEST"
  elan toolchain link "$TOOLCHAIN" "$DEST"
fi

# ---------------------------------------------------------------- Mathlib sources
cd "$LEAN_DIR"
if [ ! -d .lake/packages/mathlib ]; then
  log "lake update (the automatic cache download may fail; that is expected)"
  # `lake update` runs `cache get` as a post-update hook; bound it so a hanging download
  # does not block the setup. The git checkouts are complete before the hook starts.
  MATHLIB_NO_CACHE_ON_UPDATE=1 timeout 1800 lake update || log "lake update exited with $? (continuing)"
fi
[ -d .lake/packages/mathlib ] || { log "Mathlib checkout missing after lake update"; exit 1; }

if [ "${TRY_CACHE:-0}" = 1 ]; then
  timeout 600 lake exe cache get || log "cache get failed; building from source"
fi
[ "${SKIP_BUILD:-0}" = 1 ] && { log "SKIP_BUILD=1, stopping before the build"; exit 0; }

# ---------------------------------------------------------------- build
MODULES="$(cd "$ROOT" && python3 -m lpe.modules "${BENCHES[@]}")"
log "building $(wc -w <<< "$MODULES") target module(s) and their import closure"
# shellcheck disable=SC2086
lake build $MODULES
log "done"
