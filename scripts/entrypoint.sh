#!/usr/bin/env bash
set -euo pipefail

WORKER_NAME="${WORKER_NAME:-kali-docker-worker}"
WORKER_DIR="${CURSOR_WORKER_DIR:-/workspace}"
AGENT_BIN="${AGENT_BIN:-/root/.local/bin/agent}"
KALI_METAPACKAGE="${KALI_METAPACKAGE:-}"
RUNTIME_AUTH_FILE="/run/cursor/auth.env"
WORKER_LOG="/tmp/cursor-worker.log"
WORKER_MAX_FAILS="${WORKER_MAX_FAILS:-10}"
WORKER_AUTH_FAIL_LIMIT="${WORKER_AUTH_FAIL_LIMIT:-3}"
WORKER_RETRY_SECS="${WORKER_RETRY_SECS:-15}"

# Credenciais atualizáveis sem recriar o container (montadas do host).
if [[ -f "$RUNTIME_AUTH_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  . "$RUNTIME_AUTH_FILE"
  set +a
fi

log() {
  printf '[entrypoint] %s\n' "$*"
}

hold_container_for_debug() {
  log "Container permanece ativo para diagnóstico (evita loop de restart do Docker)."
  log "Corrija auth no host e execute: python install_kali.py auth -i <instancia>"
  log "Ou: python install_kali.py restart -i <instancia>"
  exec tail -f /dev/null
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
    if "$bin" status >/dev/null 2>&1; then
      return 0
    fi
    log "AVISO: Credenciais definidas mas 'agent status' falhou."
    return 1
  fi
  if "$bin" status >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

worker_had_auth_failure() {
  [[ -f "$WORKER_LOG" ]] && grep -qiE \
    'liveness endpoint returned 404|Failed to validate worker account|invalid.*api.?key|unauthorized|authentication failed|401|403' \
    "$WORKER_LOG"
}

run_worker_supervised() {
  local bin fail_count=0 auth_fail_count=0 backoff="$WORKER_RETRY_SECS"
  bin="$(agent_bin)"
  : >"$WORKER_LOG"

  while [[ $fail_count -lt $WORKER_MAX_FAILS ]]; do
    log "Executando: $bin worker start --name $WORKER_NAME --worker-dir $WORKER_DIR"
    set +e
    "$bin" worker start --name "$WORKER_NAME" --worker-dir "$WORKER_DIR" 2>&1 | tee -a "$WORKER_LOG"
    local code=${PIPESTATUS[0]}
    set -e

    if [[ $code -eq 0 ]]; then
      log "Worker encerrou normalmente."
      return 0
    fi

    fail_count=$((fail_count + 1))

    if worker_had_auth_failure; then
      auth_fail_count=$((auth_fail_count + 1))
      log "ERRO: Falha de autenticação/conta ao iniciar o worker Cursor."
      log "  Verifique API key em cursor.com/dashboard e atualize com:"
      log "  python install_kali.py auth --api-key <key> -i <instancia>"
      if [[ $auth_fail_count -ge $WORKER_AUTH_FAIL_LIMIT ]]; then
        hold_container_for_debug
      fi
    fi

    log "Worker saiu com código $code (tentativa $fail_count/$WORKER_MAX_FAILS). Nova tentativa em ${backoff}s..."
    sleep "$backoff"
    if [[ $backoff -lt 120 ]]; then
      backoff=$((backoff * 2))
    fi
  done

  log "Limite de tentativas do worker atingido."
  hold_container_for_debug
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
  log "  No host: python install_kali.py auth -i <instancia>"
  hold_container_for_debug
fi

ensure_git_repo

cd "$WORKER_DIR"
run_worker_supervised
