# AI Service

This project can use the remote Ollama service on `cuba001`.

## Connection

- SSH target: `yuyu@cuba001`
- Local Ollama URL: `http://127.0.0.1:11435`
- Remote Ollama URL: `http://127.0.0.1:11434`
- SSH tunnel:

  ```bash
  ssh -f -N -L 11435:127.0.0.1:11434 -o ExitOnForwardFailure=yes yuyu@cuba001
  ```

- Health/model check:

  ```bash
  curl http://127.0.0.1:11435/api/tags
  ```

## Available models

- `qwen2.5:7b` — recommended default for transcript analysis and visual-card planning
- `qwen3:8b` — larger thinking-capable model
- `qwen2.5:1.5b` — smaller and faster model
- `qwen3:4b` — middle-sized thinking-capable model

Model availability may change. The pipeline should query `/api/tags` before use and fail with a clear error if the configured model is unavailable.

Do not store passwords, API tokens, or private keys in this repository.
