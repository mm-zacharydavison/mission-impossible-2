#!/usr/bin/env bash
# One-shot setup script for the tom-cruise demo.
# Installs all system and Python dependencies.
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[x]${NC} $*"; exit 1; }

# ---------- Detect OS ----------
OS="$(uname -s)"
case "$OS" in
  Linux*)  PLATFORM=linux ;;
  Darwin*) PLATFORM=mac ;;
  *)       error "Unsupported OS: $OS" ;;
esac

# ---------- Check / install uv ----------
if ! command -v uv &>/dev/null; then
  info "Installing uv (Python package manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
info "uv $(uv --version)"

# ---------- Check / install ffmpeg ----------
if ! command -v ffmpeg &>/dev/null; then
  info "Installing ffmpeg..."
  if [ "$PLATFORM" = "linux" ]; then
    if command -v apt-get &>/dev/null; then
      sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg
    elif command -v dnf &>/dev/null; then
      sudo dnf install -y ffmpeg
    elif command -v pacman &>/dev/null; then
      sudo pacman -S --noconfirm ffmpeg
    else
      error "Cannot auto-install ffmpeg. Please install it manually."
    fi
  elif [ "$PLATFORM" = "mac" ]; then
    if command -v brew &>/dev/null; then
      brew install ffmpeg
    else
      error "Cannot auto-install ffmpeg. Please install Homebrew first: https://brew.sh"
    fi
  fi
fi
info "ffmpeg $(ffmpeg -version 2>&1 | head -1)"

# ---------- Check / install Playwright browsers ----------
install_playwright_browsers() {
  info "Installing Playwright Chromium browser..."
  uv run --with playwright python -m playwright install chromium --with-deps 2>/dev/null \
    || uv run --with playwright python -m playwright install chromium
}

# ---------- Install Python dependencies ----------
cd "$(dirname "$0")"
info "Installing Python dependencies via uv..."
uv sync

# Install Playwright browsers
install_playwright_browsers

# ---------- Install video generator dependencies ----------
info "Pre-caching video generator dependencies..."
uv run ../attention-video/generator-v1/flicker.py --help >/dev/null 2>&1

# ---------- Check for ANTHROPIC_API_KEY ----------
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  warn "ANTHROPIC_API_KEY is not set."
  warn "The video solver skill requires a valid API key."
  warn "Set it with: export ANTHROPIC_API_KEY='sk-ant-...'"
fi

echo ""
info "Setup complete!"
info "Run the demo with:  cd $(pwd) && uv run demo.py"
