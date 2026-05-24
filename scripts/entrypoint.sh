#!/usr/bin/env bash
set -euo pipefail

WORKER_NAME="${WORKER_NAME:-kali-docker-worker}"
WORKER_DIR="${CURSOR_WORKER_DIR:-/workspace}"
AGENT_BIN="${AGENT_BIN:-/root/.local/bin/agent}"

log() {
  printf '[entrypoint] %s\n' "$*"
}

ensure_agent_cli() {
  if [[ -x "$AGENT_BIN" ]] || command -v agent >/dev/null 2>&1; then
    log "Cursor CLI já instalado."
    return 0
  fi

  log "Instalando Cursor CLI (agent)..."
  curl -fsSL https://cursor.com/install | bash
}

ensure_git_repo() {
  if [[ -d "$WORKER_DIR/.git" ]]; then
    log "Repositório git encontrado em $WORKER_DIR"
    return 0
  fi

  log "AVISO: $WORKER_DIR não é um repositório git."
  log "O worker do Cursor exige um checkout git com remote configurado."
  log "Inicializando repositório vazio (apenas para testes locais)..."
  git -C "$WORKER_DIR" init -b main >/dev/null 2>&1 || git -C "$WORKER_DIR" init >/dev/null
  git -C "$WORKER_DIR" config user.email "kali-worker@local" 2>/dev/null || true
  git -C "$WORKER_DIR" config user.name "Kali Worker" 2>/dev/null || true
}

agent_bin() {
  if [[ -x "$AGENT_BIN" ]]; then
    echo "$AGENT_BIN"
  elif command -v agent >/dev/null 2>&1; then
    command -v agent
  else
    echo "agent"
  fi
}

is_authenticated() {
  local bin
  bin="$(agent_bin)"
  if [[ -n "${CURSOR_API_KEY:-}" || -n "${CURSOR_AUTH_TOKEN:-}" ]]; then
    return 0
  fi
  if "$bin" status >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

build_worker_cmd() {
  local bin
  bin="$(agent_bin)"
  local cmd=("$bin" worker start --name "$WORKER_NAME" --worker-dir "$WORKER_DIR")

  if [[ -n "${CURSOR_API_KEY:-}" ]]; then
    cmd+=(--api-key "$CURSOR_API_KEY")
  elif [[ -n "${CURSOR_AUTH_TOKEN:-}" ]]; then
    cmd+=(--auth-token "$CURSOR_AUTH_TOKEN")
  fi

  printf '%s\n' "${cmd[@]}"
}

# Modo login: apenas instala CLI e abre agent login (interativo)
if [[ "${CURSOR_AUTH_MODE:-}" == "login" ]]; then
  ensure_agent_cli
  log "Iniciando login do Cursor (siga o link no navegador)..."
  exec "$(agent_bin)" login
fi

log "Iniciando Kali Cursor Worker"
log "  WORKER_NAME=$WORKER_NAME"
log "  WORKER_DIR=$WORKER_DIR"

ensure_agent_cli

if ! is_authenticated; then
  log "ERRO: Autenticação necessária para o worker."
  log "  Opção 1 — No host: python install_kali.py login"
  log "  Opção 2 — No host: defina CURSOR_API_KEY no .env e rode install novamente"
  log "  Opção 3 — Dentro do container: agent login"
  exit 1
fi

ensure_git_repo

cd "$WORKER_DIR"

mapfile -t WORKER_CMD < <(build_worker_cmd)
log "Executando: ${WORKER_CMD[*]}"

exec "${WORKER_CMD[@]}"
