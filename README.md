# Kali Linux + Cursor Worker (Docker Desktop)

Automatiza a execução do **Kali Linux** no **Docker Desktop** (Linux, macOS ou Windows) e inicia o worker do Cursor Cloud Agent:

```bash
agent worker start --name <identificador>
```

Documentação oficial do worker: [My Machines | Cursor Docs](https://cursor.com/docs/cloud-agent/my-machines)

## O que este repositório faz

1. Faz `docker pull` da imagem oficial [`kalilinux/kali-rolling`](https://hub.docker.com/r/kalilinux/kali-rolling)
2. Monta `scripts/entrypoint.sh` no container (sem rebuild do Kali)
3. Instala dependências mínimas (`curl`, `git`, etc.) e o **Cursor CLI** (`agent`) no primeiro start
4. Sobe um container persistente com seu diretório em `/workspace` e executa `agent worker start --name <WORKER_NAME>`

## Pré-requisitos

| Item | Detalhe |
|------|---------|
| Docker Desktop | Instalado e em execução ("Running") |
| Python 3.10+ | Para rodar `install_kali.py` no host |
| Conta Cursor | Com acesso a Cloud Agents / My Machines |
| Repositório git | O diretório montado em `/workspace` deve ser um repo git (com remote, em produção) |
| Rede | Saída HTTPS para `api2.cursor.sh` e artefatos S3 (ver [networking](https://cursor.com/docs/cloud-agent/my-machines#networking)) |

## Início rápido

```bash
# Menu interativo (recomendado) — múltiplas instâncias, perfil Kali, auth
python install_kali.py

# Ou explicitamente:
python install_kali.py menu
```

```bash
# 1. Configurar variáveis (opcional; o menu também grava em instances.json)
cp .env.example .env

# 2. Instalar via CLI direto
python install_kali.py install

# Alternativa A: API key direto
# Edite .env com CURSOR_API_KEY=...  ou:
python install_kali.py install --api-key "sua-chave"

# Alternativa B: login no navegador (link do agent login)
python install_kali.py login
python install_kali.py install

# 3. Acompanhar logs
python install_kali.py logs -f

# 4. Ver status
python install_kali.py status
```

No [cursor.com/agents](https://cursor.com/agents), a máquina deve aparecer com o nome definido em `WORKER_NAME`.

## Múltiplas instâncias Kali

Cada instância tem container, worker name, diretório, perfil e auth próprios — salvo em `instances.json` (não versionado).

| Perfil | Conteúdo |
|--------|----------|
| `minimal` | curl, git + Cursor agent |
| `headless` | `kali-linux-headless` |
| `large` | `kali-linux-large` |

```bash
python install_kali.py list
python install_kali.py status -i pentest-01
python install_kali.py restart -i lab-red
```

Modelo: `instances.json.example`

## Configuração (`.env`)

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `WORKER_NAME` | Nome do worker (`--name`) no Cursor | `kali-docker-worker` |
| `CONTAINER_NAME` | Nome do container Docker | `kali-cursor-worker` |
| `IMAGE_NAME` | Imagem Docker | `kalilinux/kali-rolling:latest` |
| `WORKER_DIR` | Pasta do host montada em `/workspace` | diretório do projeto |
| `CURSOR_API_KEY` | API key para autenticação automática | (vazio) |
| `CURSOR_WORKER_DIR` | Caminho dentro do container | `/workspace` |

### CLI (sobrescreve `.env`)

```bash
python install_kali.py install --name meu-kali-lab --worker-dir C:\projetos\meu-repo
python install_kali.py install --api-key "$env:CURSOR_API_KEY"
```

## Comandos do script

| Comando | Ação |
|---------|------|
| *(sem args)* / `menu` | Menu CLI: nova instância, gerenciar, auth, perfis |
| `list` | Lista instâncias registradas |
| `install` | Pull da imagem oficial + start do container + worker |
| `-i`, `--instance` | ID em `instances.json` (ex: `-i pentest-01`) |
| `pull` | Apenas `docker pull kalilinux/kali-rolling` |
| `build` | Alias de `pull` (imagem oficial) |
| `start` | Inicia container existente ou cria um novo |
| `stop` | Para o container |
| `restart` | Para e inicia novamente |
| `remove` | Remove o container |
| `status` | Exibe configuração e estado |
| `logs` | Mostra logs (`-f` para seguir) |
| `login` | `agent login` interativo (link no navegador) |

## Docker Compose (alternativa)

```bash
cp .env.example .env
docker compose pull
docker compose up -d
docker compose logs -f
```

## Autenticação do worker

O worker exige autenticação. O script oferece duas formas:

| Método | Como usar |
|--------|-----------|
| **API key** | `CURSOR_API_KEY` no `.env`, `--api-key`, ou opção `[1]` no prompt do `install` |
| **agent login** | `python install_kali.py login` ou opção `[2]` no `install` (abre link no navegador) |

Credenciais do login ficam no volume Docker `{CONTAINER_NAME}-cursor-auth`.

```bash
# Só login (sem subir worker ainda)
python install_kali.py login

# CI / automação (sem prompts)
python install_kali.py install --non-interactive --api-key "$CURSOR_API_KEY"
```

## Estrutura do projeto

```
teste-kali/
├── install_kali.py      # Orquestrador cross-platform (host)
├── docker-compose.yml   # Usa kalilinux/kali-rolling oficial
├── scripts/entrypoint.sh
├── docs/INSTALACAO.md   # Passo a passo detalhado
├── .env.example
└── README.md
```

## Troubleshooting

| Problema | Solução |
|----------|---------|
| `Docker não está respondendo` | Abra o Docker Desktop e aguarde o ícone ficar verde |
| Worker não aparece no Cursor | Verifique `python install_kali.py logs` e se `CURSOR_API_KEY` é válida |
| `worker=...` rejeitado em Slack/GitHub | O nome deve coincidir com `WORKER_NAME` e o repo com o remote do checkout |
| Sem git no volume | Use um diretório que já seja `git clone`; o entrypoint cria repo vazio só para testes |
| Firewall corporativo | Libere saída para `api2.cursor.sh`, `api2direct.cursor.sh`, `cloud-agent-artifacts.s3.us-east-1.amazonaws.com` |

Debug do worker dentro do container:

```bash
docker exec -it kali-cursor-worker agent worker start --debug --name teste
```

## Referências

- [Kali Docker Images](https://www.kali.org/docs/containers/official-kalilinux-docker-images/)
- [Using Kali Docker Images](https://www.kali.org/docs/containers/using-kali-docker-images/)
- [Cursor — My Machines](https://cursor.com/docs/cloud-agent/my-machines)
- [Cursor — Self-Hosted Pool](https://cursor.com/docs/cloud-agent/self-hosted-pool)

## Licença

Uso livre para fins educacionais e de laboratório. Respeite os termos do Cursor e da distribuição Kali Linux.
