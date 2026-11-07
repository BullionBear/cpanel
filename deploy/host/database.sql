-- cpanel v3's database and role (C2-07), on the Postgres 17 instance at 100.65.26.119. Run once, as a superuser:
--
--   psql -v ON_ERROR_STOP=1 -v password="$PW" -d postgres -f database.sql
--
-- The role is not a superuser, cannot create roles or databases, and owns only cpanel_v3. It is not bullionbear
-- (#45). pg_hba.conf limits it further to this one database from the deploy host; see docs/operations.md.
\set ON_ERROR_STOP on

CREATE ROLE cpanel_v3 LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 40 PASSWORD :'password';

CREATE DATABASE cpanel_v3 OWNER cpanel_v3 ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE cpanel_v3 FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE cpanel_v3 TO cpanel_v3;

\connect cpanel_v3
-- Postgres 15+ already keeps CREATE on public from PUBLIC; say it anyway.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO cpanel_v3;
