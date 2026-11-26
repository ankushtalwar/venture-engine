"""Versioned Postgres schema. Files are applied in name order by `pgstore.migrate` and recorded in
`schema_migrations`; each one runs in its own transaction and is written to be safe to re-run."""
