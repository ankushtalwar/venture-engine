-- Databases created from the first draft of 0001/0002 stored whole records as JSONB, which reorders object
-- keys. Convert any such column to JSON (exact text). A no-op on databases created from the current files.

DO $$
DECLARE
    col record;
BEGIN
    FOR col IN
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = current_schema() AND udt_name = 'jsonb'
          AND (table_name, column_name) IN (('documents', 'data'), ('signals', 'data'), ('job_runs', 'data'),
                                            ('price_runs', 'data'), ('clusters', 'data'), ('overrides', 'payload'))
    LOOP
        EXECUTE format('ALTER TABLE %I ALTER COLUMN %I TYPE json USING %I::json',
                       col.table_name, col.column_name, col.column_name);
    END LOOP;
END $$;
