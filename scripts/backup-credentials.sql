-- Pass encrypted fields/configuration directly to an isolated verifier, never logs.
SELECT format('SELECT jsonb_build_object(%L, %I)::text FROM %I.%I WHERE %I IS NOT NULL;',
              column_name, column_name, table_schema, table_name, column_name)
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name LIKE 'platform\_%' ESCAPE '\'
  AND column_name IN ('encrypted_key', 'encrypted_data', 'config', 'config_snapshot')
ORDER BY table_name, column_name
\gexec
