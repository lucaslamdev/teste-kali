#!/usr/bin/env python3
"""
Instala e executa Kali Linux no Docker Desktop (Linux, macOS ou Windows)
e inicia o Cursor Cloud Agent worker: agent worker start --name <nome>.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OFFICIAL_IMAGE = "kalilinux/kali-rolling:latest"
DEFAULT_IMAGE = OFFICIAL_IMAGE
DEFAULT_CONTAINER = "kali-cursor-worker"
DEFAULT_WORKER_NAME = "kali-docker-worker"
ENTRYPOINT_SCRIPT = ROOT / "scripts" / "entrypoint.sh"
ENV_FILE = ROOT / ".env"
INSTANCES_FILE = ROOT / "instances.json"

KALI_PROFILES: dict[str, tuple[str, str]] = {
    "minimal": ("Mínimo (curl, git + Cursor agent)", ""),
    "headless": ("Kali headless (ferramentas comuns)", "kali-linux-headless"),
    "large": ("Kali large (conjunto amplo)", "kali-linux-large"),
}


def uses_official_image(image: str) -> bool:
    base = image.split(":")[0].lower()
    return base in ("kalilinux/kali-rolling", "docker.io/kalilinux/kali-rolling")


def entrypoint_mount_path() -> str:
    return host_volume_path(str(ENTRYPOINT_SCRIPT))


def apt_bootstrap_snippet() -> str:
    return """
if ! command -v curl >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends curl git ca-certificates procps
  rm -rf /var/lib/apt/lists/*
fi
"""


def cfg_from_instance(inst: dict) -> dict[str, str]:
    profile = inst.get("kali_profile", "minimal")
    _, meta = KALI_PROFILES.get(profile, KALI_PROFILES["minimal"])
    worker_dir = inst.get("worker_dir", str(ROOT))
    return {
        "WORKER_NAME": inst.get("worker_name", DEFAULT_WORKER_NAME),
        "CONTAINER_NAME": inst.get("container_name", DEFAULT_CONTAINER),
        "IMAGE_NAME": inst.get("image_name", OFFICIAL_IMAGE),
        "WORKER_DIR": str(Path(worker_dir).expanduser().resolve()),
        "CURSOR_API_KEY": inst.get("cursor_api_key", ""),
        "CURSOR_AUTH_TOKEN": inst.get("cursor_auth_token", ""),
        "CURSOR_WORKER_DIR": inst.get("cursor_worker_dir", "/workspace"),
        "KALI_PROFILE": profile,
        "KALI_METAPACKAGE": meta,
        "_instance_id": inst.get("id", ""),
    }


def default_instance_record(instance_id: str = "default") -> dict:
    env = load_dotenv(ENV_FILE)
    profile = env.get("KALI_PROFILE", "minimal")
    if profile not in KALI_PROFILES:
        profile = "minimal"
    return {
        "id": instance_id,
        "container_name": env.get("CONTAINER_NAME", DEFAULT_CONTAINER),
        "worker_name": env.get("WORKER_NAME", DEFAULT_WORKER_NAME),
        "worker_dir": env.get("WORKER_DIR", str(ROOT)),
        "image_name": env.get("IMAGE_NAME", OFFICIAL_IMAGE),
        "kali_profile": profile,
        "cursor_api_key": env.get("CURSOR_API_KEY", ""),
        "cursor_auth_token": env.get("CURSOR_AUTH_TOKEN", ""),
        "cursor_worker_dir": env.get("CURSOR_WORKER_DIR", "/workspace"),
        "auth_mode": "api_key" if env.get("CURSOR_API_KEY") else "login",
    }


def load_instances() -> dict:
    if INSTANCES_FILE.is_file():
        data = json.loads(INSTANCES_FILE.read_text(encoding="utf-8"))
        if "instances" in data:
            return data
    # Migra .env legado para instância default
    data = {
        "active": "default",
        "instances": {"default": default_instance_record("default")},
    }
    save_instances(data)
    return data


def save_instances(data: dict) -> None:
    INSTANCES_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def save_instance_record(instance_id: str, record: dict) -> None:
    data = load_instances()
    record["id"] = instance_id
    data["instances"][instance_id] = record
    data["active"] = instance_id
    save_instances(data)


def list_instance_ids() -> list[str]:
    return sorted(load_instances().get("instances", {}).keys())


def get_active_instance_id() -> str:
    data = load_instances()
    return data.get("active") or "default"


def get_instance_cfg(instance_id: str) -> dict[str, str]:
    data = load_instances()
    inst = data["instances"].get(instance_id)
    if not inst:
        raise KeyError(f"Instância '{instance_id}' não encontrada.")
    return cfg_from_instance(inst)


def discover_docker_kali_containers(docker: str) -> list[str]:
    result = run(
        docker,
        ["ps", "-a", "--format", "{{.Names}}"],
        capture=True,
    )
    names = [n.strip() for n in (result.stdout or "").splitlines() if n.strip()]
    return [n for n in names if n.startswith("kali-") or n == DEFAULT_CONTAINER]


def container_env_map(docker: str, name: str) -> dict[str, str]:
    if not container_exists(docker, name):
        return {}
    try:
        result = run(
            docker,
            ["inspect", name, "--format", "{{json .Config.Env}}"],
            capture=True,
        )
        raw = json.loads(result.stdout or "[]")
        env: dict[str, str] = {}
        for item in raw:
            if "=" in item:
                k, _, v = item.partition("=")
                env[k] = v
        return env
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}


def instance_runtime_status(docker: str, cfg: dict[str, str]) -> dict:
    name = cfg["CONTAINER_NAME"]
    running = container_running(docker, name)
    exists = container_exists(docker, name)
    env = container_env_map(docker, name) if exists else {}
    return {
        "container": name,
        "running": running,
        "exists": exists,
        "env_worker": env.get("WORKER_NAME", ""),
        "env_profile": env.get("KALI_METAPACKAGE", ""),
        "auth_volume": auth_volume_name(name),
    }


def print_instance_summary(docker: str, instance_id: str) -> None:
    cfg = get_instance_cfg(instance_id)
    rt = instance_runtime_status(docker, cfg)
    profile_label = KALI_PROFILES.get(cfg["KALI_PROFILE"], ("?", ""))[0]
    auth = "API key" if cfg.get("CURSOR_API_KEY") else (
        "login (volume)" if is_agent_authenticated(docker, cfg) else "não configurado"
    )
    state = "EM EXECUÇÃO" if rt["running"] else ("PARADO" if rt["exists"] else "NÃO CRIADO")
    print(f"  [{instance_id}] {cfg['CONTAINER_NAME']} | worker={cfg['WORKER_NAME']}")
    print(f"           perfil={profile_label} | auth={auth} | {state}")
    if rt["exists"] and rt["env_worker"] and rt["env_worker"] != cfg["WORKER_NAME"]:
        print(f"           ⚠ container usa WORKER_NAME={rt['env_worker']} (config: {cfg['WORKER_NAME']})")


def cli_ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def cli_choose(prompt: str, options: list[tuple[str, str]]) -> str:
    print(prompt)
    for key, label in options:
        print(f"  [{key}] {label}")
    valid = {k for k, _ in options}
    while True:
        choice = input("Escolha: ").strip()
        if choice in valid:
            return choice
        print("Opção inválida.")


def prompt_kali_profile() -> str:
    opts = [(k, v[0]) for k, v in KALI_PROFILES.items()]
    return cli_choose("\nPerfil Kali:", opts)


def prompt_new_instance_id(existing: list[str]) -> str:
    while True:
        raw = cli_ask("\nID da instância (ex: pentest-01, lab-red)", "")
        slug = re.sub(r"[^a-z0-9-]", "-", raw.lower()).strip("-")
        if not slug:
            print("ID inválido.")
            continue
        if slug in existing:
            print(f"ID '{slug}' já existe.")
            continue
        return slug


def flow_create_instance(docker: str) -> None:
    data = load_instances()
    existing = list(data["instances"].keys())
    iid = prompt_new_instance_id(existing)
    container_name = cli_ask("Nome do container Docker", f"kali-{iid}")
    worker_name = cli_ask("Nome do worker no Cursor (--name)", iid)
    worker_dir = cli_ask("Diretório no host (WORKER_DIR)", str(ROOT))
    profile = prompt_kali_profile()

    print("\nAutenticação Cursor:")
    auth_choice = cli_choose("", [("1", "API key"), ("2", "Login no navegador (agent login)")])

    record = {
        "id": iid,
        "container_name": container_name,
        "worker_name": worker_name,
        "worker_dir": worker_dir,
        "image_name": OFFICIAL_IMAGE,
        "kali_profile": profile,
        "cursor_api_key": "",
        "cursor_auth_token": "",
        "cursor_worker_dir": "/workspace",
        "auth_mode": "api_key" if auth_choice == "1" else "login",
    }
    save_instance_record(iid, record)
    cfg = get_instance_cfg(iid)

    if auth_choice == "1":
        try:
            cfg["CURSOR_API_KEY"] = prompt_api_key()
        except ValueError as exc:
            print(f"Erro: {exc}")
            return
        record["cursor_api_key"] = cfg["CURSOR_API_KEY"]
        record["auth_mode"] = "api_key"
        save_instance_record(iid, record)
        if is_interactive():
            ans = input("Salvar API key nesta instância (instances.json)? [S/n]: ").strip().lower()
            if ans in ("", "s", "sim", "y", "yes"):
                pass  # já salvo em record
    else:
        ensure_image(docker, cfg["IMAGE_NAME"])
        cmd_login(docker, cfg, quiet_success=True)
        record["auth_mode"] = "login"
        save_instance_record(iid, record)
        cfg = get_instance_cfg(iid)

    print(f"\n[install] Instalando instância '{iid}'...")
    cmd_install(docker, cfg, non_interactive=True)
    print(f"\n[ok] Instância '{iid}' pronta.")


def flow_manage_instance(docker: str, instance_id: str) -> None:
    while True:
        cfg = get_instance_cfg(instance_id)
        rt = instance_runtime_status(docker, cfg)
        print()
        print("=" * 60)
        print(f"  Instância: {instance_id}")
        print("=" * 60)
        print_instance_summary(docker, instance_id)
        print()
        print("  [1] Instalar / recriar container (install)")
        print("  [2] Iniciar (start)")
        print("  [3] Parar (stop)")
        print("  [4] Reiniciar (restart)")
        print("  [5] Status detalhado")
        print("  [6] Logs")
        print("  [7] Autenticação — API key")
        print("  [8] Autenticação — agent login")
        print("  [9] Trocar perfil Kali (minimal/headless/large)")
        print("  [10] Remover container e registro da instância")
        print("  [0] Voltar")
        choice = input("\nEscolha: ").strip()

        if choice == "0":
            return
        if choice == "1":
            cmd_install(docker, cfg, non_interactive=True)
        elif choice == "2":
            cfg = ensure_authentication(docker, cfg, non_interactive=False)
            start_container(docker, cfg)
        elif choice == "3":
            stop_container(docker, cfg["CONTAINER_NAME"])
        elif choice == "4":
            stop_container(docker, cfg["CONTAINER_NAME"])
            remove_container(docker, cfg["CONTAINER_NAME"])
            cfg = ensure_authentication(docker, cfg, non_interactive=False)
            start_container(docker, cfg)
        elif choice == "5":
            show_status(docker, cfg)
        elif choice == "6":
            follow = input("Seguir logs em tempo real? [s/N]: ").strip().lower() in ("s", "sim", "y", "yes")
            show_logs(docker, cfg["CONTAINER_NAME"], follow)
        elif choice == "7":
            try:
                key = prompt_api_key()
            except ValueError as exc:
                print(f"Erro: {exc}")
                continue
            data = load_instances()
            rec = data["instances"][instance_id]
            rec["cursor_api_key"] = key
            rec["auth_mode"] = "api_key"
            save_instance_record(instance_id, rec)
            cfg = get_instance_cfg(instance_id)
            ans = input("Recriar container agora? [S/n]: ").strip().lower()
            if ans in ("", "s", "sim", "y", "yes"):
                stop_container(docker, cfg["CONTAINER_NAME"])
                remove_container(docker, cfg["CONTAINER_NAME"])
                start_container(docker, cfg)
        elif choice == "8":
            ensure_image(docker, cfg["IMAGE_NAME"])
            cmd_login(docker, cfg)
            ans = input("Recriar container com login salvo? [S/n]: ").strip().lower()
            if ans in ("", "s", "sim", "y", "yes"):
                data = load_instances()
                rec = data["instances"][instance_id]
                rec["auth_mode"] = "login"
                rec["cursor_api_key"] = ""
                save_instance_record(instance_id, rec)
                cfg = get_instance_cfg(instance_id)
                stop_container(docker, cfg["CONTAINER_NAME"])
                remove_container(docker, cfg["CONTAINER_NAME"])
                start_container(docker, cfg)
        elif choice == "9":
            profile = prompt_kali_profile()
            data = load_instances()
            rec = data["instances"][instance_id]
            rec["kali_profile"] = profile
            save_instance_record(instance_id, rec)
            print("[info] Perfil alterado. Recrie o container (opção 1 ou 4).")
        elif choice == "10":
            cfg = get_instance_cfg(instance_id)
            stop_container(docker, cfg["CONTAINER_NAME"])
            remove_container(docker, cfg["CONTAINER_NAME"])
            data = load_instances()
            data["instances"].pop(instance_id, None)
            if data.get("active") == instance_id:
                data["active"] = next(iter(data["instances"]), "")
            save_instances(data)
            print(f"[ok] Instância '{instance_id}' removida.")
            return
        else:
            print("Opção inválida.")


def flow_import_orphan(docker: str) -> None:
    known = {load_instances()["instances"][i]["container_name"] for i in list_instance_ids()}
    orphans = [n for n in discover_docker_kali_containers(docker) if n not in known]
    if not orphans:
        print("\nNenhum container Kali órfão encontrado.")
        return
    print("\nContainers Kali não registrados:")
    for i, name in enumerate(orphans, 1):
        print(f"  [{i}] {name}")
    raw = input("Importar qual? (número ou 0=cancelar): ").strip()
    if raw == "0" or not raw.isdigit():
        return
    idx = int(raw) - 1
    if idx < 0 or idx >= len(orphans):
        print("Índice inválido.")
        return
    cname = orphans[idx]
    iid = cli_ask("ID para esta instância", cname.replace("kali-", "", 1) or "imported")
    env = container_env_map(docker, cname)
    record = {
        "id": iid,
        "container_name": cname,
        "worker_name": env.get("WORKER_NAME", iid),
        "worker_dir": str(ROOT),
        "image_name": OFFICIAL_IMAGE,
        "kali_profile": "minimal",
        "cursor_api_key": "",
        "cursor_auth_token": "",
        "cursor_worker_dir": "/workspace",
        "auth_mode": "login",
    }
    if env.get("KALI_METAPACKAGE") == "kali-linux-headless":
        record["kali_profile"] = "headless"
    elif env.get("KALI_METAPACKAGE") == "kali-linux-large":
        record["kali_profile"] = "large"
    save_instance_record(iid, record)
    print(f"[ok] Instância '{iid}' importada.")


def run_interactive_menu(docker: str) -> None:
    while True:
        data = load_instances()
        active = data.get("active", "default")
        print()
        print("=" * 60)
        print("  Kali Docker + Cursor Worker — Menu")
        print("=" * 60)
        print(f"  Instância ativa: {active}")
        print()
        ids = list_instance_ids()
        if ids:
            print("Instâncias registradas:")
            for iid in ids:
                print_instance_summary(docker, iid)
                if iid == active:
                    print("           (instância ativa)")
        else:
            print("  (nenhuma instância)")
        print()
        print("  [1] Nova instância Kali")
        print("  [2] Gerenciar instância existente")
        print("  [3] Definir instância ativa")
        print("  [4] Importar container Kali existente")
        print("  [5] Baixar imagem oficial (pull)")
        print("  [0] Sair")
        choice = input("\nEscolha: ").strip()

        if choice == "0":
            print("Até logo.")
            return
        if choice == "1":
            flow_create_instance(docker)
        elif choice == "2":
            if not ids:
                print("Crie uma instância primeiro.")
                continue
            iid = cli_choose(
                "\nQual instância?",
                [(i, load_instances()["instances"][i]["container_name"]) for i in ids],
            )
            data["active"] = iid
            save_instances(data)
            flow_manage_instance(docker, iid)
        elif choice == "3":
            if not ids:
                print("Nenhuma instância.")
                continue
            iid = cli_choose("\nInstância ativa:", [(i, i) for i in ids])
            data["active"] = iid
            save_instances(data)
            print(f"[ok] Ativa: {iid}")
        elif choice == "4":
            flow_import_orphan(docker)
        elif choice == "5":
            ensure_image(docker, OFFICIAL_IMAGE)
            print("[ok] Imagem atualizada.")
        else:
            print("Opção inválida.")


def load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def merge_config(args: argparse.Namespace) -> dict[str, str]:
    file_env = load_dotenv(ROOT / ".env")
    os_env = {k: v for k, v in os.environ.items() if v}

    def pick(key: str, arg_value: str | None, default: str = "") -> str:
        if arg_value:
            return arg_value
        return os_env.get(key) or file_env.get(key) or default

    instance_id = getattr(args, "instance", None) or ""
    if instance_id or INSTANCES_FILE.is_file():
        inst_data = load_instances()
        iid = instance_id or inst_data.get("active", "")
        if iid and iid in inst_data.get("instances", {}):
            cfg = cfg_from_instance(inst_data["instances"][iid])
            if args.name:
                cfg["WORKER_NAME"] = args.name
            if args.container_name:
                cfg["CONTAINER_NAME"] = args.container_name
            if args.image:
                cfg["IMAGE_NAME"] = args.image
            if args.worker_dir:
                cfg["WORKER_DIR"] = str(Path(args.worker_dir).expanduser().resolve())
            if args.api_key:
                cfg["CURSOR_API_KEY"] = args.api_key
            if args.auth_token:
                cfg["CURSOR_AUTH_TOKEN"] = args.auth_token
            if args.worker_dir_in_container:
                cfg["CURSOR_WORKER_DIR"] = args.worker_dir_in_container
            return cfg

    worker_dir = pick("WORKER_DIR", args.worker_dir, str(ROOT))
    profile = pick("KALI_PROFILE", getattr(args, "kali_profile", None), "minimal")
    _, meta = KALI_PROFILES.get(profile, KALI_PROFILES["minimal"])
    return {
        "WORKER_NAME": pick("WORKER_NAME", args.name, DEFAULT_WORKER_NAME),
        "CONTAINER_NAME": pick("CONTAINER_NAME", args.container_name, DEFAULT_CONTAINER),
        "IMAGE_NAME": pick("IMAGE_NAME", args.image, DEFAULT_IMAGE),
        "WORKER_DIR": str(resolve_path_from_root(worker_dir)),
        "CURSOR_API_KEY": pick("CURSOR_API_KEY", args.api_key, ""),
        "CURSOR_AUTH_TOKEN": pick("CURSOR_AUTH_TOKEN", args.auth_token, ""),
        "CURSOR_WORKER_DIR": pick("CURSOR_WORKER_DIR", args.worker_dir_in_container, "/workspace"),
        "KALI_PROFILE": profile,
        "KALI_METAPACKAGE": meta,
        "_instance_id": "",
    }



def resolve_path_from_root(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def cursor_auth_env_file_args(cfg: dict[str, str]) -> tuple[list[str], Path | None]:
    """Pass auth vars via --env-file so secrets are not visible in docker CLI argv."""
    pairs: list[tuple[str, str]] = []
    if cfg.get("CURSOR_API_KEY"):
        pairs.append(("CURSOR_API_KEY", cfg["CURSOR_API_KEY"]))
    if cfg.get("CURSOR_AUTH_TOKEN"):
        pairs.append(("CURSOR_AUTH_TOKEN", cfg["CURSOR_AUTH_TOKEN"]))
    if not pairs:
        return [], None

    fd, path_str = tempfile.mkstemp(prefix="cursor-docker-env-", suffix=".env")
    os.close(fd)
    path = Path(path_str)
    path.write_text("\n".join(f"{key}={value}" for key, value in pairs) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return ["--env-file", str(path)], path



def find_docker() -> str:
    docker = shutil.which("docker")
    if docker:
        return docker
    if platform.system() == "Windows":
        candidates = [
            Path(os.environ.get("ProgramFiles", "")) / "Docker/Docker/resources/bin/docker.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Docker/Docker/resources/docker.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError(
        "Docker não encontrado no PATH. Instale o Docker Desktop e reinicie o terminal."
    )


DOCKER_TIMEOUT_SEC = 15


def run(
    docker: str,
    args: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    timeout: float | None = DOCKER_TIMEOUT_SEC,
) -> subprocess.CompletedProcess[str]:
    cmd = [docker, *args]
    return subprocess.run(
        cmd,
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=capture,
        timeout=timeout,
    )


def docker_available(docker: str) -> bool:
    try:
        run(docker, ["info"], capture=True, timeout=DOCKER_TIMEOUT_SEC)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def container_exists(docker: str, name: str) -> bool:
    result = run(
        docker,
        ["ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        capture=True,
    )
    return name in (result.stdout or "").strip().splitlines()


def container_running(docker: str, name: str) -> bool:
    result = run(
        docker,
        ["ps", "--filter", f"name=^{name}$", "--filter", "status=running", "--format", "{{.Names}}"],
        capture=True,
    )
    return name in (result.stdout or "").strip().splitlines()


def auth_home_volume_name(container_name: str) -> str:
    return f"{container_name}-cursor-auth-home"


def auth_config_volume_name(container_name: str) -> str:
    return f"{container_name}-cursor-auth-config"


def auth_volume_name(container_name: str) -> str:
    return auth_home_volume_name(container_name)


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def update_env_file(key: str, value: str) -> None:
    lines: list[str] = []
    if ENV_FILE.is_file():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    found = False
    updated: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            updated.append(f"{key}={value}")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def has_credentials(cfg: dict[str, str]) -> bool:
    return bool(cfg.get("CURSOR_API_KEY") or cfg.get("CURSOR_AUTH_TOKEN"))


def run_agent_in_container(
    docker: str,
    cfg: dict[str, str],
    shell_cmd: str,
    *,
    interactive: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Executa comando no contexto da imagem (com volume de auth)."""
    image = cfg["IMAGE_NAME"]
    auth_home = auth_home_volume_name(cfg["CONTAINER_NAME"])
    auth_config = auth_config_volume_name(cfg["CONTAINER_NAME"])
    worker_mount = host_volume_path(cfg["WORKER_DIR"])

    cmd: list[str] = [
        docker,
        "run",
        "--rm",
        "-v",
        f"{worker_mount}:/workspace",
        "-v",
        f"{auth_home}:/root/.cursor",
        "-v",
        f"{auth_config}:/root/.config/cursor",
    ]
    if interactive:
        cmd.insert(2, "-it")
    env_file_args, env_file_cleanup = cursor_auth_env_file_args(cfg)
    cmd.extend(env_file_args)
    if extra_env:
        for k, v in extra_env.items():
            cmd.extend(["-e", f"{k}={v}"])
    # Sobrescreve ENTRYPOINT da imagem; senão shell_cmd vira argv ignorado do entrypoint.
    cmd.extend(["--entrypoint", "bash", image, "-c", shell_cmd])

    try:
        return subprocess.run(
            cmd,
            cwd=ROOT,
            check=False,
            text=True,
            timeout=None if interactive else 120,
        )
    finally:
        if env_file_cleanup is not None:
            env_file_cleanup.unlink(missing_ok=True)


def is_agent_authenticated(docker: str, cfg: dict[str, str]) -> bool:
    if has_credentials(cfg):
        return True

    script = (
        apt_bootstrap_snippet()
        + """
AGENT_BIN="${AGENT_BIN:-/root/.local/bin/agent}"
if ! [[ -x "$AGENT_BIN" ]] && ! command -v agent >/dev/null 2>&1; then
  curl -fsSL https://cursor.com/install | bash
fi
"$AGENT_BIN" status
"""
    )
    result = run_agent_in_container(docker, cfg, script, interactive=False)
    if result is None:
        return False
    return result.returncode == 0


def prompt_save_api_key(key: str) -> None:
    if not is_interactive():
        return
    answer = input("Salvar CURSOR_API_KEY no arquivo .env? [s/N]: ").strip().lower()
    if answer in ("s", "sim", "y", "yes"):
        if not ENV_FILE.is_file() and (ROOT / ".env.example").is_file():
            shutil.copy(ROOT / ".env.example", ENV_FILE)
        update_env_file("CURSOR_API_KEY", key)
        print(f"[ok] Chave salva em {ENV_FILE}")


def prompt_api_key() -> str:
    print()
    print("Informe sua API key do Cursor.")
    print("Obtenha em: https://cursor.com/dashboard")
    print()
    key = getpass.getpass("CURSOR_API_KEY (entrada oculta): ").strip()
    if not key:
        raise ValueError("API key vazia.")
    return key


def prompt_auth_method() -> str:
    print()
    print("=" * 60)
    print("  Autenticação necessária para o Cursor agent worker")
    print("=" * 60)
    print()
    print("  [1] Informar API key (CURSOR_API_KEY)")
    print("      Recomendado para automação e reinícios do container.")
    print()
    print("  [2] Login no navegador (agent login)")
    print("      Abre um link para você autorizar a conta Cursor.")
    print()
    print("  [0] Cancelar")
    print()

    while True:
        choice = input("Escolha [1/2/0]: ").strip()
        if choice in ("0", "1", "2"):
            return choice
        print("Opção inválida. Digite 1, 2 ou 0.")


def cmd_login(docker: str, cfg: dict[str, str], *, quiet_success: bool = False) -> None:
    if not ENV_FILE.is_file() and (ROOT / ".env.example").is_file():
        print(f"[info] Crie {ENV_FILE} a partir de .env.example se quiser persistir config.")

    print()
    print("Iniciando 'agent login' no container...")
    print("Quando aparecer o link, abra no navegador e conclua a autorização.")
    print()

    login_script = (
        apt_bootstrap_snippet()
        + """
AGENT_BIN="${AGENT_BIN:-/root/.local/bin/agent}"
if ! [[ -x "$AGENT_BIN" ]] && ! command -v agent >/dev/null 2>&1; then
  curl -fsSL https://cursor.com/install | bash
fi
exec "$AGENT_BIN" login
"""
    )
    result = run_agent_in_container(docker, cfg, login_script, interactive=True)
    if result is None:
        raise RuntimeError("Falha ao executar login.")
    if result.returncode != 0:
        print("[erro] Login não concluído.", file=sys.stderr)
        raise SystemExit(result.returncode)

    print()
    print("[ok] Login concluído. Credenciais salvas nos volumes Docker:")
    print(f"     {auth_home_volume_name(cfg['CONTAINER_NAME'])}")
    print(f"     {auth_config_volume_name(cfg['CONTAINER_NAME'])}")
    if not quiet_success:
        print()
        print("Agora execute: python install_kali.py install")


def ensure_authentication(
    docker: str,
    cfg: dict[str, str],
    *,
    non_interactive: bool = False,
) -> dict[str, str]:
    """Garante API key ou sessão agent login antes de subir o worker."""
    if has_credentials(cfg):
        return cfg

    if is_agent_authenticated(docker, cfg):
        print("[auth] Sessão existente encontrada (agent login anterior).")
        return cfg

    if non_interactive or not is_interactive():
        print(
            "Erro: autenticação necessária.\n"
            "  - Defina CURSOR_API_KEY no .env ou no ambiente, ou\n"
            "  - Execute: python install_kali.py login",
            file=sys.stderr,
        )
        raise SystemExit(1)

    choice = prompt_auth_method()
    if choice == "0":
        print("Cancelado.")
        raise SystemExit(0)
    if choice == "1":
        try:
            cfg["CURSOR_API_KEY"] = prompt_api_key()
        except ValueError as exc:
            print(f"Erro: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        prompt_save_api_key(cfg["CURSOR_API_KEY"])
        return cfg

    # choice == "2"
    ensure_image(docker, cfg["IMAGE_NAME"])
    cmd_login(docker, cfg, quiet_success=True)
    if not is_agent_authenticated(docker, cfg):
        print("[erro] Login não detectado após agent login.", file=sys.stderr)
        raise SystemExit(1)
    return cfg


def host_volume_path(path: str) -> str:
    resolved = Path(path).resolve()
    if platform.system() == "Windows":
        # Docker Desktop aceita caminhos Windows com barras normais
        return str(resolved).replace("\\", "/")
    return str(resolved)


def pull_image(docker: str, image: str) -> None:
    print(f"[pull] Baixando imagem oficial {image}...")
    print(f"       Fonte: https://hub.docker.com/r/kalilinux/kali-rolling")
    run(docker, ["pull", image], timeout=None)


def ensure_image(docker: str, image: str) -> None:
    """Garante que a imagem existe localmente (pull da oficial por padrão)."""
    if uses_official_image(image):
        pull_image(docker, image)
        return
    try:
        run(docker, ["image", "inspect", image], capture=True)
        print(f"[ok] Imagem local encontrada: {image}")
    except subprocess.CalledProcessError:
        print(
            f"Erro: imagem '{image}' não encontrada. "
            f"Use IMAGE_NAME={OFFICIAL_IMAGE} ou faça docker pull manualmente.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def start_container(docker: str, cfg: dict[str, str]) -> None:
    name = cfg["CONTAINER_NAME"]
    image = cfg["IMAGE_NAME"]
    worker_mount = host_volume_path(cfg["WORKER_DIR"])

    if container_running(docker, name):
        print(f"[ok] Container '{name}' já está em execução.")
        return

    if container_exists(docker, name):
        print(f"[start] Recriando container '{name}' para aplicar configuração atual...")
        run(docker, ["rm", "-f", name], check=False)

    auth_home = auth_home_volume_name(name)
    auth_config = auth_config_volume_name(name)
    env_file_args, env_file_cleanup = cursor_auth_env_file_args(cfg)
    cmd: list[str] = [
        "run",
        "-d",
        "--restart",
        "unless-stopped",
        "--name",
        name,
        "-v",
        f"{worker_mount}:/workspace",
        "-v",
        f"{auth_home}:/root/.cursor",
        "-v",
        f"{auth_config}:/root/.config/cursor",
        "-e",
        f"WORKER_NAME={cfg['WORKER_NAME']}",
        "-e",
        f"CURSOR_WORKER_DIR={cfg['CURSOR_WORKER_DIR']}",
    ]
    cmd.extend(env_file_args)
    if cfg.get("KALI_METAPACKAGE"):
        cmd.extend(["-e", f"KALI_METAPACKAGE={cfg['KALI_METAPACKAGE']}"])
    if uses_official_image(image):
        if not ENTRYPOINT_SCRIPT.is_file():
            print(f"Erro: {ENTRYPOINT_SCRIPT} não encontrado.", file=sys.stderr)
            raise SystemExit(1)
        cmd.extend(
            [
                "-v",
                f"{entrypoint_mount_path()}:/usr/local/bin/entrypoint.sh:ro",
                "-w",
                "/workspace",
                "--entrypoint",
                "/usr/local/bin/entrypoint.sh",
            ]
        )
    cmd.append(image)

    print(f"[run] Subindo container '{name}' (worker: {cfg['WORKER_NAME']})...")
    if uses_official_image(image):
        print(f"       Imagem: {image} (oficial Kali)")
    try:
        run(docker, cmd)
    finally:
        if env_file_cleanup is not None:
            env_file_cleanup.unlink(missing_ok=True)


def stop_container(docker: str, name: str) -> None:
    if not container_exists(docker, name):
        print(f"[info] Container '{name}' não existe.")
        return
    print(f"[stop] Parando '{name}'...")
    run(docker, ["stop", name], check=False, timeout=60)


def remove_container(docker: str, name: str) -> None:
    if not container_exists(docker, name):
        print(f"[info] Container '{name}' não existe.")
        return
    run(docker, ["rm", "-f", name], check=False)
    print(f"[ok] Container '{name}' removido.")


def show_logs(docker: str, name: str, follow: bool) -> None:
    args = ["logs", name]
    if follow:
        args.insert(1, "-f")
    subprocess.run([docker, *args], cwd=ROOT)


def show_status(docker: str, cfg: dict[str, str]) -> None:
    name = cfg["CONTAINER_NAME"]
    print(f"Sistema host : {platform.system()} {platform.release()}")
    print(f"Docker       : {docker}")
    print(f"Container    : {name}")
    print(f"Imagem       : {cfg['IMAGE_NAME']}")
    print(f"Worker name  : {cfg['WORKER_NAME']}")
    profile = cfg.get("KALI_PROFILE", "minimal")
    print(f"Perfil Kali  : {KALI_PROFILES.get(profile, ('?', ''))[0]}")
    print(f"Volume host  : {cfg['WORKER_DIR']} -> /workspace")
    if cfg.get("_instance_id"):
        print(f"Instância    : {cfg['_instance_id']}")
    if cfg.get("CURSOR_API_KEY"):
        auth_label = "API key (.env/CLI)"
    elif cfg.get("CURSOR_AUTH_TOKEN"):
        auth_label = "auth token"
    else:
        auth_label = "agent login (volume)" if is_agent_authenticated(docker, cfg) else "(não autenticado)"
    print(f"Autenticação : {auth_label}")
    print(f"Volume auth  : {auth_home_volume_name(name)}, {auth_config_volume_name(name)}")
    print()
    if container_running(docker, name):
        print(f"Status: EM EXECUÇÃO")
        run(docker, ["ps", "--filter", f"name=^{name}$"], capture=False)
    elif container_exists(docker, name):
        print("Status: PARADO (existe)")
    else:
        print("Status: NÃO CRIADO")


def cmd_install(
    docker: str,
    cfg: dict[str, str],
    *,
    non_interactive: bool = False,
) -> None:
    ensure_image(docker, cfg["IMAGE_NAME"])
    cfg = ensure_authentication(docker, cfg, non_interactive=non_interactive)
    start_container(docker, cfg)
    print()
    print("Próximos passos:")
    print(f"  1. Ver logs : python install_kali.py logs")
    print(f"  2. Status   : python install_kali.py status")
    print(f"  3. No Cursor: https://cursor.com/agents — selecione a máquina '{cfg['WORKER_NAME']}'")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kali Linux no Docker Desktop + Cursor agent worker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python install_kali.py              # menu interativo
  python install_kali.py menu
  python install_kali.py install -i pentest-01
  python install_kali.py list
  python install_kali.py status -i lab-01
  python install_kali.py logs -f
        """,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default=None,
        choices=[
            "menu",
            "install",
            "pull",
            "build",
            "start",
            "stop",
            "restart",
            "remove",
            "status",
            "logs",
            "login",
            "list",
        ],
        help="Ação (sem argumento = menu interativo)",
    )
    parser.add_argument(
        "--instance",
        "-i",
        help="ID da instância em instances.json",
    )
    parser.add_argument(
        "--kali-profile",
        choices=list(KALI_PROFILES.keys()),
        help="Perfil: minimal, headless ou large",
    )
    parser.add_argument("--name", help="Nome do worker (--name do agent worker start)")
    parser.add_argument("--container-name", help="Nome do container Docker")
    parser.add_argument("--image", help="Tag da imagem Docker")
    parser.add_argument("--worker-dir", help="Diretório no host montado em /workspace")
    parser.add_argument(
        "--worker-dir-in-container",
        help="Diretório dentro do container (padrão: /workspace)",
    )
    parser.add_argument("--api-key", help="CURSOR_API_KEY para autenticação do worker")
    parser.add_argument("--auth-token", help="Token alternativo (CURSOR_AUTH_TOKEN)")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Não perguntar auth; exige CURSOR_API_KEY ou login prévio",
    )
    parser.add_argument("-f", "--follow", action="store_true", help="Seguir logs (comando logs)")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cfg = merge_config(args)

    try:
        docker = find_docker()
    except FileNotFoundError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    if not docker_available(docker):
        print(
            "Erro: Docker não está respondendo (timeout ou daemon parado). "
            "Abra o Docker Desktop e aguarde ficar 'Running'.",
            file=sys.stderr,
        )
        return 1

    command = args.command
    if command is None:
        if is_interactive():
            run_interactive_menu(docker)
            return 0
        parser.print_help()
        return 0

    non_interactive = args.non_interactive

    if command == "menu":
        run_interactive_menu(docker)
    elif command == "list":
        for iid in list_instance_ids():
            print_instance_summary(docker, iid)
    elif command == "install":
        cmd_install(docker, cfg, non_interactive=non_interactive)
    elif command in ("pull", "build"):
        ensure_image(docker, cfg["IMAGE_NAME"])
    elif command == "login":
        ensure_image(docker, cfg["IMAGE_NAME"])
        cmd_login(docker, cfg)
    elif command == "start":
        cfg = ensure_authentication(docker, cfg, non_interactive=non_interactive)
        start_container(docker, cfg)
    elif command == "stop":
        stop_container(docker, cfg["CONTAINER_NAME"])
    elif command == "restart":
        stop_container(docker, cfg["CONTAINER_NAME"])
        remove_container(docker, cfg["CONTAINER_NAME"])
        cfg = ensure_authentication(docker, cfg, non_interactive=non_interactive)
        start_container(docker, cfg)
    elif command == "remove":
        remove_container(docker, cfg["CONTAINER_NAME"])
    elif command == "status":
        show_status(docker, cfg)
    elif command == "logs":
        show_logs(docker, cfg["CONTAINER_NAME"], args.follow)
    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
