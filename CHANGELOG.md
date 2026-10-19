# Changelog

## 0.1.0 — 2026-10-19

First public release.

- Hands-free voice conversation in Turkish and English: Silero VAD, local Whisper
  (large-v3-turbo on NVIDIA GPUs and 16+ thread CPUs, small elsewhere), streaming local
  Pocket TTS, barge-in.
- Speaker naming from speech: self-introductions, nominations ("Ahmet, go ahead"),
  corrections; unknown voices become "Person N" / "N. kişi"; uncertain turns are marked
  unclear instead of guessed.
- Consent-first, encrypted voice profiles (AES-256-GCM, key in the OS keychain).
- Brains: OpenAI (spend-capped), OpenClaw, Hermes Agent, Ollama, LM Studio, any
  OpenAI-compatible Chat Completions or Responses server, webhooks, or none.
- Built-in MCP server (Streamable HTTP at `/mcp`, stdio via `utterlane mcp`) and REST API;
  Agent Skill for OpenClaw, Hermes Agent, Claude Code and Codex.
- One-folder installers for Windows, macOS (Apple Silicon) and Linux with automatic CUDA/CPU
  selection and hash-verified model downloads.
