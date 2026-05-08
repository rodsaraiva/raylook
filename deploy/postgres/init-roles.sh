#!/bin/bash
# Roda antes do schema.sql. Configura senha pra role raylook_api (que é
# criada pelo schema.sql como NOLOGIN — aqui ligamos LOGIN com a senha
# vinda da env do PostgREST).
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'raylook_api') THEN
            CREATE ROLE raylook_api LOGIN PASSWORD '${POSTGRES_PASSWORD}';
        ELSE
            ALTER ROLE raylook_api LOGIN PASSWORD '${POSTGRES_PASSWORD}';
        END IF;
    END \$\$;
EOSQL
