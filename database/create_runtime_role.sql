-- Run once in the Supabase SQL Editor as the project owner.
-- Replace the password placeholder before running, then store the resulting
-- connection string only in Vercel. Never commit the real password.

create role optiprime_runtime
  login
  nosuperuser
  nocreatedb
  nocreaterole
  noinherit
  password 'REPLACE_WITH_A_LONG_RANDOM_PASSWORD';

grant connect on database postgres to optiprime_runtime;
grant usage on schema public to optiprime_runtime;
grant select, insert, update, delete on all tables in schema public to optiprime_runtime;
grant usage, select on all sequences in schema public to optiprime_runtime;

alter default privileges in schema public
  grant select, insert, update, delete on tables to optiprime_runtime;
alter default privileges in schema public
  grant usage, select on sequences to optiprime_runtime;
