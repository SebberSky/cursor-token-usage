#!/usr/bin/env bash
# Cut a versioned release and point a channel (stable|beta) at it.
#
# Usage:
#   ./scripts/release.sh 0.2.0 stable
#   ./scripts/release.sh 0.3.0-beta.1 beta
#   ./scripts/release.sh 0.2.0 stable --dry-run
#   ./scripts/release.sh 0.2.0 stable --push
#
# Does not push by default. Pass --push to push commit + tag.
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

VERSION="${1:-}"
CHANNEL="${2:-}"
DRY_RUN=0
DO_PUSH=0

for arg in "${@:3}"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    --push) DO_PUSH=1 ;;
    *)
      echo "Unknown option: ${arg}" >&2
      exit 1
      ;;
  esac
done

if [[ -z "${VERSION}" || -z "${CHANNEL}" ]]; then
  echo "Usage: $0 <version> <stable|beta> [--dry-run] [--push]" >&2
  exit 1
fi

if [[ ! "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-].+)?$ ]]; then
  echo "Version must look like 1.2.3 or 1.2.3-beta.1 (got: ${VERSION})" >&2
  exit 1
fi

case "${CHANNEL}" in
  stable|beta) ;;
  *)
    echo "Channel must be stable or beta (got: ${CHANNEL})" >&2
    exit 1
    ;;
esac

if [[ "${CHANNEL}" == "stable" && "${VERSION}" == *beta* ]]; then
  echo "Refusing stable channel with a beta version: ${VERSION}" >&2
  exit 1
fi

TAG="v${VERSION}"
EXTENSION_VERSION="${VERSION}"
if [[ "${VERSION}" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)-beta\.([0-9]+)$ ]]; then
  # VS Code requires x.y.z — beta.N maps to .y.(1000+N)
  EXTENSION_VERSION="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.$((1000 + BASH_REMATCH[4]))"
elif [[ "${VERSION}" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)- ]]; then
  EXTENSION_VERSION="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.$((BASH_REMATCH[3] + 900))"
elif [[ "${VERSION}" =~ ^([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
  EXTENSION_VERSION="${BASH_REMATCH[1]}"
fi

PUBLISHED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
CHANNEL_FILE="channels/${CHANNEL}.json"

echo "Release plan"
echo "  version   : ${VERSION}"
echo "  channel   : ${CHANNEL}"
echo "  tag       : ${TAG}"
echo "  extension : ${EXTENSION_VERSION}"
echo "  published : ${PUBLISHED_AT}"
echo "  push      : ${DO_PUSH}"

if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo "(dry-run — no files written)"
  exit 0
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree not clean — commit or stash before releasing" >&2
  exit 1
fi

if git rev-parse "${TAG}" >/dev/null 2>&1; then
  echo "Tag already exists: ${TAG}" >&2
  exit 1
fi

export RELEASE_ROOT="${ROOT}"
export RELEASE_VERSION="${VERSION}"
export RELEASE_CHANNEL="${CHANNEL}"
export RELEASE_TAG="${TAG}"
export RELEASE_EXT_VERSION="${EXTENSION_VERSION}"
export RELEASE_PUBLISHED_AT="${PUBLISHED_AT}"

python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["RELEASE_ROOT"])
version = os.environ["RELEASE_VERSION"]
channel = os.environ["RELEASE_CHANNEL"]
tag = os.environ["RELEASE_TAG"]
ext_version = os.environ["RELEASE_EXT_VERSION"]
published = os.environ["RELEASE_PUBLISHED_AT"]

(root / "version.json").write_text(
    json.dumps(
        {"name": "cursor-token-usage", "version": version, "channel": channel},
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)

pkg_path = root / "extension" / "package.json"
pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
pkg["version"] = ext_version
pkg_path.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")

channel_path = root / "channels" / f"{channel}.json"
channel_path.parent.mkdir(parents=True, exist_ok=True)
channel_path.write_text(
    json.dumps(
        {
            "channel": channel,
            "version": version,
            "tag": tag,
            "ref": tag,
            "published_at": published,
            "extension_version": ext_version,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
print(f"updated version.json, extension/package.json, {channel_path}")
PY

git add version.json extension/package.json "${CHANNEL_FILE}"
git commit -m "$(cat <<EOF
Release ${TAG} (${CHANNEL})

EOF
)"

git tag -a "${TAG}" -m "cursor-token-usage ${VERSION} (${CHANNEL})"

if [[ "${DO_PUSH}" -eq 1 ]]; then
  git push origin HEAD
  git push origin "${TAG}"
  echo "Pushed commit + tag ${TAG}"
else
  echo
  echo "Local release ready."
  echo "  Review:  git show ${TAG}"
  echo "  Publish: git push origin HEAD && git push origin ${TAG}"
  echo "  Or:      ./scripts/release.sh ${VERSION} ${CHANNEL} --push"
fi
