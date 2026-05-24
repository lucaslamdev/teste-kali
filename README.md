# Kali Linux + Cursor Worker (Docker Desktop)

Automatiza a execução do **Kali Linux** no **Docker Desktop** (Linux, macOS ou Windows) e inicia o worker do Cursor Cloud Agent:

```bash
agent worker start --name <identificador>
```

Documentação oficial do worker: [My Machines | Cursor Docs](https://cursor.com/docs/cloud-agent/my-machines)

## O que este repositório faz

1. Constrói uma imagem baseada em `kalilinux/kali-rolling`
2. Instala dependências (`git`, `curl`, etc.) e o **Cursor CLI** (`agent`) no primeiro start
3. Sobe um container persistente com seu diretório de trabalho montado em `/workspace`
4. Executa `agent worker start --name <WORKER_NAME>` para registrar a máquina no Cursor

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
# 1. Configurar variáveis (opcional se usar login interativo)
cp .env.example .env

# 2. Instalar — o script pergunta como autenticar se necessário
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

## Configuração (`.env`)

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `WORKER_NAME` | Nome do worker (`--name`) no Cursor | `kali-docker-worker` |
| `CONTAINER_NAME` | Nome do container Docker | `kali-cursor-worker` |
| `IMAGE_NAME` | Tag da imagem local | `kali-cursor-worker:latest` |
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
| `install` | Build da imagem + start do container + worker (padrão) |
| `build` | Apenas constrói a imagem Docker |
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
docker compose build
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
├── Dockerfile           # Imagem Kali + entrypoint
├── docker-compose.yml
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
