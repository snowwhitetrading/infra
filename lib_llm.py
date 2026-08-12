# -*- coding: utf-8 -*-
"""lib_llm.py — client Claude cho CI (dùng CLAUDE_CODE_OAUTH_TOKEN gói Max, fallback API key).
Tạo token: `claude setup-token` → dán vào GitHub secret. Không có token thì script LLM bỏ qua."""
import os
from anthropic import Anthropic

OAUTH_HEADERS = {
    "anthropic-beta": "claude-code-20250219,oauth-2025-04-20,fine-grained-tool-streaming-2025-05-14",
    "anthropic-dangerous-direct-browser-access": "true",
    "user-agent": "claude-cli/2.1.75", "x-app": "cli",
}
IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
_mode = None


def get_client():
    global _mode
    tok = os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
    if tok:
        _mode = "oauth"
        return Anthropic(api_key=None, auth_token=tok, default_headers=OAUTH_HEADERS)
    key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
    if key:
        _mode = "api"
        return Anthropic(api_key=key)
    raise SystemExit("Thiếu CLAUDE_CODE_OAUTH_TOKEN hoặc ANTHROPIC_API_KEY — bỏ qua radar LLM.")


def build_system(text):
    return [{"type": "text", "text": IDENTITY}, {"type": "text", "text": text}] if _mode == "oauth" else text
