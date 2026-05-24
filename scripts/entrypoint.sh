#!/usr/bin/env bash
set -euo pipefail

WORKER_NAME="${WORKER_NAME:-kali-docker-worker}"
WORKER_DIR="${CURSOR_WORKER_DIR:-/workspace}"
AGENT_BIN="${AGENT_BIN:-/root/.local/bin/agent}"
KALI_METAPACKAGE="${KALI_METAPACKAGE:-}"

log() {
  printf '[entrypoint] %s\n' "$*"
}

ensure_os_packages() {
  if command -v curl >/dev/null 2>&1 && command -v git >/dev/null 2>&1; then
    return 0
  fi
  export DEBIAN_FRONTEND=noninteractive
  log "Instalando dependências na imagem oficial (curl, git, ca-certificates)..."
  apt-get update -qq
  apt-get install -y --no-install-recommends curl git ca-certificates procps
  rm -rf /var/lib/apt/lists/*
}

ensure_kali_metapackage() {
  if [[ -z "$KALI_METAPACKAGE" ]]; then
    return 0
  fi
  if dpkg -s "$KALI_METAPACKAGE" >/dev/null 2>&1; then
    log "Metapacote $KALI_METAPACKAGE já instalado."
    return 0
  fi
  export DEBIAN_FRONTEND=noninteractive
  log "Instalando metapacote Kali: $KALI_METAPACKAGE (pode demorar)..."
  apt-get update -qq
  apt-get install -y "$KALI_METAPACKAGE"
  rm -rf /var/lib/apt/lists/*
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

if [[ "${CURSOR_AUTH_MODE:-}" == "login" ]]; then
  ensure_os_packages
  ensure_agent_cli
  log "Iniciando login do Cursor (siga o link no navegador)..."
  exec "$(agent_bin)" login
fi

log "Iniciando Kali Cursor Worker (kalilinux/kali-rolling)"
log "  WORKER_NAME=$WORKER_NAME"
log "  WORKER_DIR=$WORKER_DIR"
[[ -n "$KALI_METAPACKAGE" ]] && log "  KALI_METAPACKAGE=$KALI_METAPACKAGE"

ensure_os_packages
ensure_kali_metapackage
ensure_agent_cli

if ! is_authenticated; then
  log "ERRO: Autenticação necessária para o worker."
  log "  No host: python install_kali.py menu → Gerenciar instância → Autenticação"
  exit 1
fi

ensure_git_repo

cd "$WORKER_DIR"

mapfile -t WORKER_CMD < <(build_worker_cmd)
log "Executando: ${WORKER_CMD[*]}"

exec "${WORKER_CMD[@]}"
