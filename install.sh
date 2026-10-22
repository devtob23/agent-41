#!/usr/bin/env bash
# install.sh: sets up claude-box, codex-box and/or ollama-box for the current user.
# Run from the repository folder:   ./install.sh <claude|codex|ollama|all>
# Help:                              ./install.sh --help
set -euo pipefail
BOX_VERSION="0.3.0"

REPO_DIR="$(cd "$(dirname "$0")" && pwd -P)"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
CONFIG_HOME="$HOME/.config"
STAMP="$(date +%Y%m%d-%H%M%S)"
MAX_ARGS=10

die() {
  echo "install.sh: $*" >&2
  exit 1
}

usage() {
  cat <<EOF
install.sh – sets up one or more boxes

USAGE
  ./install.sh <box> [<box> ...] [--bin <folder>]

BOXES
  claude     claude-box  (Claude Code in a container)
  codex      codex-box   (Codex in a container)
  ollama     ollama-box  (local inference; internet for tools after approval)
  all        all three

OPTIONS
  --bin <folder>   Target folder for the scripts (default: ~/.local/bin)
  -h, --help       Show this help

What happens:
  Script         -> ~/.local/bin/<box>
  Containerfiles -> ~/.config/<box>/
Existing files with different content are first backed up as <file>.bak-<timestamp>.
EOF
}

# Copies a file and backs up an existing target file that differs.
install_file() {
  local src="$1"
  local dest="$2"
  local mode="$3"
  [ -f "$src" ] || die "File missing in the repository: ${src}"
  [ -n "$dest" ] || die "internal error: no target for ${src}."
  if [ -f "$dest" ] && ! cmp -s "$src" "$dest"; then
    cp "$dest" "${dest}.bak-${STAMP}" || die "Backup of ${dest} failed."
    echo "  backed up: ${dest}.bak-${STAMP}"
  fi
  cp "$src" "$dest" || die "Copying to ${dest} failed."
  chmod "$mode" "$dest" || die "Could not set permissions on ${dest}."
  echo "  ${dest}"
}

# List of Containerfiles per box (one line, space-separated).
box_files() {
  case "$1" in
    claude-box) echo "Containerfile.claude-base" ;;
    codex-box) echo "Containerfile.codex-base" ;;
    ollama-box) echo "Containerfile.agent-shell Containerfile.agent-aider Containerfile.agent-claude Containerfile.agent-opencode Containerfile.internet-proxy validate-resource.py internet-access.py internet-proxy.py" ;;
    *) die "Unknown box '$1'." ;;
  esac
}

install_box() {
  local box="$1"
  local files
  files="$(box_files "$box")"
  echo "${box}:"
  mkdir -p "$BIN_DIR" "${CONFIG_HOME}/${box}" || die "Could not create folders."
  install_file "${REPO_DIR}/${box}/${box}" "${BIN_DIR}/${box}" 755
  local f
  for f in $files; do
    install_file "${REPO_DIR}/${box}/${f}" "${CONFIG_HOME}/${box}/${f}" 644
  done
}

# --- Platform ---
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) die "On Windows, please run inside WSL2 (see README, section Windows)." ;;
esac
[ "$(id -u)" != "0" ] || die "Do not run as root; the boxes are meant for rootless Podman."

# --- Arguments ---
BOXES=""
n=0
while [ $# -gt 0 ] && [ "$n" -lt "$MAX_ARGS" ]; do
  n=$((n + 1))
  case "$1" in
    --version) echo "agent-41 ${BOX_VERSION}"; exit 0 ;;
    -h|--help) usage; exit 0 ;;
    --bin) [ $# -ge 2 ] || die "--bin needs a folder."; BIN_DIR="$2"; shift 2 ;;
    claude|claude-box) BOXES="${BOXES} claude-box"; shift ;;
    codex|codex-box) BOXES="${BOXES} codex-box"; shift ;;
    ollama|ollama-box) BOXES="${BOXES} ollama-box"; shift ;;
    all) BOXES=" claude-box codex-box ollama-box"; shift ;;
    *) die "Unknown argument '$1'. Help: ./install.sh --help" ;;
  esac
done
[ $# -eq 0 ] || die "Too many arguments (at most ${MAX_ARGS} options/boxes)."
[ -n "$BOXES" ] || { usage; exit 1; }

for box in $BOXES; do
  install_box "$box"
done

# --- Notes ---
echo
case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *) echo "Note: ${BIN_DIR} is not in PATH. Add to ~/.profile:"
     echo "  export PATH=\"${BIN_DIR}:\$PATH\"" ;;
esac
command -v podman >/dev/null 2>&1 || echo "Note: podman is not installed (see README, Requirements)."
echo "Installed version: ${BOX_VERSION}. The first start asks for the projects root."
echo "Optional for automated runs, e.g. in ~/.profile:"
for box in $BOXES; do
  case "$box" in
    claude-box) echo "  export CLAUDE_BOX_ROOT=\"\$HOME/yourProjectPath\"" ;;
    codex-box) echo "  export CODEX_BOX_ROOT=\"\$HOME/yourProjectPath\"" ;;
    ollama-box) echo "  export OLLAMA_BOX_ROOT=\"\$HOME/yourProjectPath\"" ;;
  esac
done
case "$BOXES" in
  *claude-box*) echo "Template for projects (.claude/settings.json): ${REPO_DIR}/claude-box/templates/" ;;
esac
echo "The agent itself is installed into the container image on the box's first start."
case "$BOXES" in
  *ollama-box*) echo "GPU for ollama-box: offered on the first start (or: ollama-box --setup-gpu)." ;;
esac
echo "Then start it in a project, e.g.:  cd ~/yourProjectPath/<project> && <box>"
