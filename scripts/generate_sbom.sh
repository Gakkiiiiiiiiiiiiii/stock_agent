#!/usr/bin/env sh
set -eu
# Uses the installed Syft binary when available; never downloads tools or
# contacts production services from this script.
if command -v syft >/dev/null 2>&1; then
  syft dir:. -o spdx-json="${1:-artifacts/sbom.spdx.json}"
else
  echo "syft is required to generate an SBOM (install it in CI)" >&2
  exit 2
fi
