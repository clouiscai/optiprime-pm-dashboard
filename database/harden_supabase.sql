-- Secure this server-backed app from Supabase's public Data API.
-- Run as the project owner in the Supabase SQL Editor after schema migrations.
-- The FastAPI backend connects directly through DATABASE_URL and is unaffected.

begin;

do $$
declare
    table_record record;
begin
    for table_record in
        select schemaname, tablename
        from pg_tables
        where schemaname = 'public'
    loop
        execute format(
            'alter table %I.%I enable row level security',
            table_record.schemaname,
            table_record.tablename
        );
    end loop;
end
$$;

-- This application does not access tables through Supabase REST or GraphQL.
revoke all privileges on all tables in schema public
    from anon, authenticated, service_role;
revoke all privileges on all sequences in schema public
    from anon, authenticated, service_role;
revoke execute on all functions in schema public
    from anon, authenticated, service_role;
revoke execute on all functions in schema public from public;

-- Prevent future migrations run by the project owner from reopening objects.
alter default privileges for role postgres in schema public
    revoke all privileges on tables from anon, authenticated, service_role;
alter default privileges for role postgres in schema public
    revoke all privileges on sequences from anon, authenticated, service_role;
alter default privileges for role postgres in schema public
    revoke execute on functions from anon, authenticated, service_role;
alter default privileges for role postgres in schema public
    revoke execute on functions from public;

commit;
