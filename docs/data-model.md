# Dataset ownership

`users` stores the account ID, unique normalized email address, password hash, and creation timestamp. `datasets.owner_id` is a required, indexed foreign key to `users.id`. Samples, topics, and sentiment results inherit access from their dataset.

## Upgrade an existing database

For a fresh database, `flask --app backend/app init-db` creates the full schema. For a database initialized before accounts were added, it creates `users` but leaves the existing `datasets` table as it was. Run `init-db`, start the API, and register the account or accounts that should own your existing datasets.

Then run the following in PostgreSQL against the same database. The example assigns every existing dataset to one account. If your datasets have different owners, replace the `UPDATE` with one update per owner and add `AND datasets.id IN (...)` with that owner's dataset IDs. Enter the email returned by registration.

```sql
BEGIN;

ALTER TABLE datasets ADD COLUMN owner_id integer;

UPDATE datasets
SET owner_id = users.id
FROM users
WHERE users.email = 'owner@example.test';

ALTER TABLE datasets
    ALTER COLUMN owner_id SET NOT NULL,
    ADD CONSTRAINT fk_datasets_owner FOREIGN KEY (owner_id) REFERENCES users (id);

CREATE INDEX ix_datasets_owner_id ON datasets (owner_id);

COMMIT;
```

If any dataset has no owner, PostgreSQL rejects `SET NOT NULL` and the transaction cannot commit. Roll it back and correct the assignments before retrying.
