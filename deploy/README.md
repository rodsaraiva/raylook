# Deploy do Raylook

Stack Swarm 100% isolado — postgres dedicado, network privada, integração externa via Traefik.

## Arquitetura

```
   Internet
       │
       ▼ TLS (letsencrypt)
   ┌───────────────┐
   │   Traefik     │  network_swarm_public
   └───────┬───────┘
           │
   ┌───────▼─────────────────────────────────┐
   │ raylook-dashboard (FastAPI)             │
   │   network_swarm_public + raylook_internal
   └───────┬─────────────────────────────────┘
           │ raylook_internal (privada)
   ┌───────▼─────────┐
   │ raylook-postgrest │
   └───────┬─────────┘
           │
   ┌───────▼──────────┐
   │ raylook-postgres │ (volume raylook_pgdata)
   └──────────────────┘
```

Postgres não tem porta no host. PostgREST não tem porta no host. Único contato com o resto do mundo é o dashboard via Traefik.

## Pré-requisitos no VPS (uma vez)

```bash
# Network privada (separada do network_swarm_public)
docker network create -d overlay --attachable=false raylook_internal

# Diretório de deploy fora do código (env não vai pro git)
mkdir -p /root/projects/raylook-deploy
cd /root/projects/raylook-deploy

# Copiar stack + scripts vindos do repo
ln -s /root/rodrigo/raylook/deploy/docker-stack.yml .
ln -s /root/rodrigo/raylook/deploy/postgres ./postgres

# Criar .env (gerar secrets fortes)
cp /root/rodrigo/raylook/.env.production.example .env
# editar .env e preencher tudo
chmod 600 .env
```

## Primeiro deploy

```bash
cd /root/projects/raylook-deploy

# Login no GHCR (uma vez)
docker login ghcr.io  # PAT com scope read:packages

# Carregar env e subir stack
export $(grep -v '^#' .env | xargs)
docker stack deploy -c docker-stack.yml raylook --with-registry-auth

# Verificar
docker stack services raylook
docker service logs raylook_raylook-postgres -f &
docker service logs raylook_raylook-dashboard -f
```

## Validação

- `https://raylook.v4smc.com/health` → `{"status":"ok"}`
- `docker exec -it $(docker ps -qf name=raylook_raylook-postgres) psql -U raylook_owner -d raylook -c "\dt"` → 14 tabelas
- `docker network inspect raylook_internal` → 3 containers conectados, nenhum mais

## Rollback

Voltar pra imagem anterior:

```bash
export DOCKER_IMAGE=ghcr.io/rodsaraiva/raylook:<sha-anterior>
docker stack deploy -c docker-stack.yml raylook --with-registry-auth
```

Ou remover stack inteiro (mantém volumes):

```bash
docker stack rm raylook
```

## Garantias de isolamento

| Garantia | Como verificar |
|---|---|
| Postgres não acessível do host | `psql -h <vps-ip> -p 5432` falha (sem `ports:` no compose) |
| Sem credencial compartilhada | senha + JWT são gerados pra esse stack só, nada batendo com Alana/Bras |
| Network privada | `docker network inspect raylook_internal` mostra só os 3 services raylook |
| Imagem própria | `ghcr.io/rodsaraiva/raylook` (registry separado) |
| Volume separado | `docker volume ls \| grep raylook` mostra `raylook_pgdata` e `raylook_data` próprios |

## CI/CD

Push em `main` dispara `.github/workflows/deploy.yml`:
1. Build da image multi-stage.
2. Push pro `ghcr.io/rodsaraiva/raylook:latest` + `:<sha>`.
3. SSH no VPS, `docker stack deploy` com a tag nova.

Secrets do GitHub Actions necessários:
- `DEPLOY_HOST` — IP/hostname do VPS
- `DEPLOY_USER` — usuário SSH (geralmente `root`)
- `DEPLOY_SSH_KEY` — chave privada SSH com acesso ao VPS
