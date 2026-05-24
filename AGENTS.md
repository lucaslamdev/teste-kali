# AGENTS.md

## Cursor Cloud specific instructions

### Overview

This is a Python + Bash utility that provisions **Kali Linux Docker containers** and starts the **Cursor Cloud Agent worker** inside them. The main orchestrator is `install_kali.py` (Python 3.10+, stdlib only — no pip dependencies). The container entrypoint is `scripts/entrypoint.sh`.

### Running the application

- **Docker is required.** The daemon must be running before executing any command.
- The script has no external Python dependencies — only stdlib.
- All commands are documented in the README. Key ones:
  - `python3 install_kali.py --help` — show CLI options
  - `python3 install_kali.py status` — show config and container state
  - `python3 install_kali.py pull` — pull the official Kali image
  - `python3 install_kali.py install --non-interactive --api-key "$CURSOR_API_KEY"` — create and start a container
- Authentication (`CURSOR_API_KEY` or `agent login`) is required before the worker container can run. Without it, `install` will fail in non-interactive mode.

### Linting

- `python3 -m py_compile install_kali.py` — syntax check
- `python3 -m ruff check install_kali.py` — lint (ruff is pre-installed via pip)
- `bash -n scripts/entrypoint.sh` — bash syntax check
- Pre-existing lint warnings (unused var, f-strings without placeholders) exist and are not blockers.

### Known gotchas

- `python3 install_kali.py list` may fail with `PermissionError` when run as non-root, because it tries to inspect Docker volume mount paths directly on the filesystem. Run with `sudo` if needed, or use `status` for a single instance instead.
- The `status` command internally pulls the Kali image if it's not cached, which takes a few seconds on first run.
- The interactive menu (`python3 install_kali.py` or `python3 install_kali.py menu`) blocks on stdin — always use explicit subcommands with `--non-interactive` in automation.

### Docker setup in Cloud Agent VM

Docker must be installed and configured with `fuse-overlayfs` storage driver and `iptables-legacy` before the daemon starts. The daemon must be started manually with `sudo dockerd` and the Docker socket needs `chmod 666 /var/run/docker.sock` for non-root access.
