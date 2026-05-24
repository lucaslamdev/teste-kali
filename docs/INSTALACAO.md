# Guia de instalação — Kali no Docker + Cursor Worker

Passo a passo para Linux, macOS e Windows (host com Docker Desktop).

---

## Passo 1 — Instalar Docker Desktop

### Windows

1. Baixe em [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/)
2. Instale e reinicie se solicitado
3. Abra o Docker Desktop e aguarde **Engine running**
4. (Opcional) Ative integração WSL2 se usar WSL

### macOS

1. Baixe Docker Desktop para Apple Silicon ou Intel
2. Arraste para Applications e abra
3. Conceda permissões do sistema quando pedido

### Linux

1. Instale Docker Desktop para Linux **ou** Docker Engine + Compose
2. Adicione seu usuário ao grupo `docker`: `sudo usermod -aG docker $USER`
3. Faça logout/login

**Verificar:**

```bash
docker version
docker info
```

---

## Passo 2 — Instalar Python no host

Python 3.10 ou superior, apenas para rodar o script de automação no host.

```bash
python --version
# ou
python3 --version
```

---

## Passo 3 — Clonar ou copiar este repositório

```bash
cd /caminho/para/teste-kali            # Windows (ex.: C:\projetos\teste-kali)
# cd ~/projetos/teste-kali             # Linux/macOS
```

Inicialize git se ainda não for um repositório (necessário para o worker):

```bash
git init
git remote add origin https://github.com/SEU_USUARIO/SEU_REPO.git
```

---

## Passo 4 — Configurar variáveis de ambiente

```bash
cp .env.example .env
```

Edite `.env` (mínimo: `WORKER_NAME`; autenticação veja abaixo):

```env
WORKER_NAME=meu-kali-lab
CONTAINER_NAME=kali-cursor-worker
WORKER_DIR=.
```

---

## Passo 4b — Autenticação (obrigatório)

Escolha **uma** das opções:

### Opção A — API key no `.env` (recomendado)

```env
CURSOR_API_KEY=sua_chave_aqui
```

Obtenha em [cursor.com/dashboard](https://cursor.com/dashboard) → API Keys.

### Opção B — Login no navegador

```bash
python install_kali.py login
```

O terminal exibirá um **link** do `agent login`. Abra no navegador e autorize. As credenciais ficam no volume Docker `kali-cursor-worker-cursor-auth`.

### Opção C — Prompt durante o `install`

Se não houver `CURSOR_API_KEY` nem login prévio, ao rodar `install` o script pergunta:

```
  [1] Informar API key (CURSOR_API_KEY)
  [2] Login no navegador (agent login)
  [0] Cancelar
```

### Trocar autenticação depois (sem recriar o container)

Com o worker em execução, use o menu **Gerenciar instância → [7] Alterar autenticação** ou:

```bash
python install_kali.py auth -i default
python install_kali.py auth --api-key "nova-chave" -i default
python install_kali.py auth --login -i default
```

O script atualiza `instances.json` / `.cursor-auth/<container>.env` e reinicia o container com `docker restart`. Só recria o container se ele foi criado antes desta feature (sem volume `/run/cursor/auth.env`) ou se você passar `--recreate`.

---

## Passo 5 — Executar instalação automática

No diretório do projeto:

```bash
python install_kali.py install
```

O script irá:

1. Detectar o sistema operacional do host
2. Localizar o executável `docker`
3. Verificar se o daemon está ativo
4. Baixar a imagem oficial `kalilinux/kali-rolling:latest` ([Docker Hub](https://hub.docker.com/r/kalilinux/kali-rolling))
5. Criar e iniciar o container (entrypoint montado do host)
6. No container: instalar dependências/`agent` (se necessário) e executar `agent worker start --name <WORKER_NAME>`

**Com nome customizado via CLI:**

```bash
python install_kali.py install --name pentest-box-01 --worker-dir /caminho/para/seu/repo
```

---

## Passo 6 — Validar que o worker está ativo

```bash
python install_kali.py status
python install_kali.py logs -f
```

Dentro do container (opcional):

```bash
docker exec -it kali-cursor-worker bash
agent --version
agent status
```

No navegador: [cursor.com/agents](https://cursor.com/agents) → selecione o ambiente com o nome `WORKER_NAME`.

---

## Passo 7 — Usar a máquina nomeada em integrações

Com `WORKER_NAME=meu-kali-lab`:

| Superfície | Exemplo |
|------------|---------|
| Slack | `@Cursor worker=meu-kali-lab corrigir o teste` |
| GitHub | `@cursoragent worker=meu-kali-lab revisar PR` |
| Linear | `worker=meu-kali-lab` no corpo da issue |

O checkout em `/workspace` deve corresponder ao repositório do trigger.

---

## Operações do dia a dia

```bash
# Parar
python install_kali.py stop

# Reiniciar
python install_kali.py restart

# Remover container (imagem permanece)
python install_kali.py remove

# Atualizar imagem oficial e reiniciar
python install_kali.py pull
python install_kali.py restart
```

---

## Instalação manual (sem Python)

```bash
docker pull kalilinux/kali-rolling:latest
docker run -d \
  --name kali-cursor-worker \
  -v "$(pwd):/workspace" \
  -v "$(pwd)/scripts/entrypoint.sh:/usr/local/bin/entrypoint.sh:ro" \
  -w /workspace \
  --entrypoint /usr/local/bin/entrypoint.sh \
  -e WORKER_NAME=meu-kali \
  -e CURSOR_API_KEY=sua_chave \
  kalilinux/kali-rolling:latest
```

Windows (PowerShell):

```powershell
docker pull kalilinux/kali-rolling:latest
docker run -d `
  --name kali-cursor-worker `
  -v "${PWD}:/workspace" `
  -v "${PWD}/scripts/entrypoint.sh:/usr/local/bin/entrypoint.sh:ro" `
  -w /workspace `
  --entrypoint /usr/local/bin/entrypoint.sh `
  -e WORKER_NAME=meu-kali `
  -e CURSOR_API_KEY=sua_chave `
  kalilinux/kali-rolling:latest
```

---

## Instalar ferramentas Kali no container

A imagem oficial não inclui metapacotes por padrão. Entre no container:

```bash
docker exec -it kali-cursor-worker bash
apt update && apt install -y kali-linux-headless
```

---

## Solução de problemas

### `Docker não encontrado no PATH`

- Windows: reinstale Docker Desktop e marque "Add to PATH"
- Reinicie o terminal após instalar

### Container reinicia em loop

O entrypoint **não encerra mais** após falhas repetidas de auth — o container fica ativo para diagnóstico. A política Docker é `on-failure:2` (no máximo 2 reinícios automáticos).

```bash
python install_kali.py logs
```

Causas comuns do worker não subir:

- **API key inválida ou sem permissão** — mensagem `liveness endpoint returned 404` nos logs
- Sem rede para `api2.cursor.sh`
- `agent` ainda instalando metapacote Kali (aguarde)

Corrija a key e aplique sem recriar:

```bash
python install_kali.py auth --api-key "sua-chave" -i default
```

### Refazer autenticação

```bash
python install_kali.py login
python install_kali.py restart
```

Ou defina `CURSOR_API_KEY` no `.env` e:

```bash
python install_kali.py remove
python install_kali.py install
```

### Preflight Cursor

```bash
docker exec -it kali-cursor-worker agent worker start --debug --name meu-kali
```

---

## Próximos passos

- Para metapacote completo: `apt install -y kali-linux-headless` dentro do container
- Use labels e pools com `--pool` para ambientes Enterprise ([Self-Hosted Pool](https://cursor.com/docs/cloud-agent/self-hosted-pool))
- Automatize em CI com `CURSOR_API_KEY` em secrets
