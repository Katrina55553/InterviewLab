-- Only aggregate counts and migration heads; never emit application row contents.
SELECT format('SELECT %L, count(*) FROM %I.%I;', schemaname || '.' || tablename, schemaname, tablename)
FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename
\gexec
SELECT format('SELECT %L || version_num FROM %I.%I ORDER BY version_num;', 'alembic:', schemaname, tablename)
FROM pg_tables WHERE schemaname = 'public' AND tablename = 'platform_alembic_version'
\gexec
