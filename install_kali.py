#!/usr/bin/env python3
"""
Instala e executa Kali Linux no Docker Desktop (Linux, macOS ou Windows)
e inicia o Cursor Cloud Agent worker: agent worker start --name <nome>.
"""

from __future__ import annotations

import argparse
import getpass
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_IMAGE = "kali-cursor-worker:latest"
DEFAULT_CONTAINER = "kali-cursor-worker"
DEFAULT_WORKER_NAME = "kali-docker-worker"
ENV_FILE = ROOT / ".env"


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

    worker_dir = pick("WORKER_DIR", args.worker_dir, str(ROOT))
    return {
        "WORKER_NAME": pick("WORKER_NAME", args.name, DEFAULT_WORKER_NAME),
        "CONTAINER_NAME": pick("CONTAINER_NAME", args.container_name, DEFAULT_CONTAINER),
        "IMAGE_NAME": pick("IMAGE_NAME", args.image, DEFAULT_IMAGE),
        "WORKER_DIR": str(resolve_path_from_root(worker_dir)),
        "CURSOR_API_KEY": pick("CURSOR_API_KEY", args.api_key, ""),
        "CURSOR_AUTH_TOKEN": pick("CURSOR_AUTH_TOKEN", args.auth_token, ""),
        "CURSOR_WORKER_DIR": pick("CURSOR_WORKER_DIR", args.worker_dir_in_container, "/workspace"),
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

    if ENV_FILE.is_file():
        on_disk = load_dotenv(ENV_FILE)
        if all(on_disk.get(key) == value for key, value in pairs):
            return ["--env-file", str(ENV_FILE)], None

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


def auth_volume_name(container_name: str) -> str:
    return f"{container_name}-cursor-auth"


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
    auth_vol = auth_volume_name(cfg["CONTAINER_NAME"])
    worker_mount = host_volume_path(cfg["WORKER_DIR"])

    cmd: list[str] = [
        docker,
        "run",
        "--rm",
        "-v",
        f"{worker_mount}:/workspace",
        "-v",
        f"{auth_vol}:/root/.cursor",
        "-v",
        f"{auth_vol}:/root/.config/cursor",
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

    script = """
set -e
if ! command -v agent >/dev/null 2>&1; then
  curl -fsSL https://cursor.com/install | bash
fi
agent status
"""
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

    login_script = """
set -e
if ! command -v agent >/dev/null 2>&1; then
  curl -fsSL https://cursor.com/install | bash
fi
exec agent login
"""
    result = run_agent_in_container(docker, cfg, login_script, interactive=True)
    if result is None:
        raise RuntimeError("Falha ao executar login.")
    if result.returncode != 0:
        print("[erro] Login não concluído.", file=sys.stderr)
        raise SystemExit(result.returncode)

    print()
    print("[ok] Login concluído. Credenciais salvas no volume Docker:")
    print(f"     {auth_volume_name(cfg['CONTAINER_NAME'])}")
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
    try:
        run(docker, ["image", "inspect", cfg["IMAGE_NAME"]], capture=True)
    except subprocess.CalledProcessError:
        build_image(docker, cfg["IMAGE_NAME"])
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


def build_image(docker: str, image: str) -> None:
    print(f"[build] Construindo imagem {image}...")
    run(docker, ["build", "-t", image, str(ROOT)], timeout=None)


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

    auth_vol = auth_volume_name(name)
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
        f"{auth_vol}:/root/.cursor",
        "-v",
        f"{auth_vol}:/root/.config/cursor",
        "-e",
        f"WORKER_NAME={cfg['WORKER_NAME']}",
        "-e",
        f"CURSOR_WORKER_DIR={cfg['CURSOR_WORKER_DIR']}",
    ]
    cmd.extend(env_file_args)
    cmd.append(image)

    print(f"[run] Subindo container '{name}' (worker: {cfg['WORKER_NAME']})...")
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
    print(f"Volume host  : {cfg['WORKER_DIR']} -> /workspace")
    auth_vol = auth_volume_name(name)
    if cfg.get("CURSOR_API_KEY"):
        auth_label = "API key (.env/CLI)"
    elif cfg.get("CURSOR_AUTH_TOKEN"):
        auth_label = "auth token"
    else:
        auth_label = "agent login (volume)" if is_agent_authenticated(docker, cfg) else "(não autenticado)"
    print(f"Autenticação : {auth_label}")
    print(f"Volume auth  : {auth_vol}")
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
    build_image(docker, cfg["IMAGE_NAME"])
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
  python install_kali.py install --name meu-kali
  python install_kali.py login
  python install_kali.py install --api-key "sua-chave"
  python install_kali.py status
  python install_kali.py logs -f
  python install_kali.py stop
        """,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="install",
        choices=[
            "install",
            "build",
            "start",
            "stop",
            "restart",
            "remove",
            "status",
            "logs",
            "login",
        ],
        help="Ação a executar (padrão: install)",
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
    non_interactive = args.non_interactive

    if command == "install":
        cmd_install(docker, cfg, non_interactive=non_interactive)
    elif command == "build":
        build_image(docker, cfg["IMAGE_NAME"])
    elif command == "login":
        try:
            run(docker, ["image", "inspect", cfg["IMAGE_NAME"]], capture=True)
        except subprocess.CalledProcessError:
            build_image(docker, cfg["IMAGE_NAME"])
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
