import argparse
import os
from pathlib import Path

import psycopg2
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="Apply and verify Supabase Data API hardening.")
    parser.add_argument("env_file", nargs="?", type=Path, help="Environment file containing DATABASE_URL")
    args = parser.parse_args()

    database_url = (
        dotenv_values(args.env_file).get("DATABASE_URL")
        if args.env_file
        else os.getenv("DATABASE_URL")
    )
    if not database_url or not database_url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("Provide a PostgreSQL DATABASE_URL in the environment or an environment file.")

    sql = (ROOT / "database" / "harden_supabase.sql").read_text(encoding="utf-8")
    with psycopg2.connect(database_url, connect_timeout=20) as connection:
        with connection.cursor() as cursor:
            cursor.execute("select current_user")
            current_user = cursor.fetchone()[0]
            if current_user != "postgres":
                raise SystemExit(f"Hardening requires the project owner; connected as {current_user!r}.")
            cursor.execute(sql)

    with psycopg2.connect(database_url, connect_timeout=20) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select count(*), count(*) filter (where c.relrowsecurity)
                from pg_class c
                join pg_namespace n on n.oid = c.relnamespace
                where n.nspname = 'public' and c.relkind in ('r', 'p')
                """
            )
            table_count, rls_count = cursor.fetchone()
            cursor.execute(
                """
                select count(*)
                from information_schema.role_table_grants
                where table_schema = 'public'
                  and grantee in ('anon', 'authenticated', 'service_role')
                """
            )
            data_api_grants = cursor.fetchone()[0]
            cursor.execute(
                """
                select count(*)
                from pg_default_acl d
                cross join lateral aclexplode(d.defaclacl) a
                join pg_roles grantee on grantee.oid = a.grantee
                join pg_roles owner_role on owner_role.oid = d.defaclrole
                join pg_namespace n on n.oid = d.defaclnamespace
                where owner_role.rolname = 'postgres'
                  and n.nspname = 'public'
                  and grantee.rolname in ('anon', 'authenticated', 'service_role')
                """
            )
            data_api_default_grants = cursor.fetchone()[0]

    print(f"Database role: {current_user}")
    print(f"RLS enabled: {rls_count}/{table_count} public tables")
    print(f"Data API table grants remaining: {data_api_grants}")
    print(f"Data API owner-default grants remaining: {data_api_default_grants}")

    if rls_count != table_count or data_api_grants or data_api_default_grants:
        raise SystemExit("Supabase hardening verification failed.")


if __name__ == "__main__":
    main()
