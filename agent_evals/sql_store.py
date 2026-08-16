"""`SqlRunStore` — run records in Postgres, immutability enforced by the database.

The JSONL `RunStore` stays the local default; this is the store the shared
trend view reads (RC1-263). Same narrow interface — `append` / `get` / `all` —
so a consumer constructs one or the other and nothing else changes.

Two properties the text file could not provide:

* **Append-only, enforced.** A `BEFORE UPDATE OR DELETE` trigger raises, the
  same guarantee the plan store gets from SQLite `RAISE` triggers (ADR-0012).
  It protects rows, not the table: the owning credential can still `DROP
  TABLE`, which is the honest limit of trigger-based immutability.
* **Queryable.** Each record lands as JSONB beside extracted columns for the
  fields the trend view groups and filters on, so "every run where a subject
  regressed after a model change" is a query rather than a full scan.

Connection notes. The DSN comes from `EVAL_DATABASE_URL`, set once in a shell
profile outside any repo — one copy, not five drifting ones. `sslmode`
defaults to `require`, matching `resumes/reid_basic`; tests relax it because
the CI service container speaks no TLS. Heroku rotates credentials during
maintenance, so an auth failure usually means "re-run `heroku config:get
DATABASE_URL`", not a leak.
"""

from __future__ import annotations

from agent_evals.record import DuplicateRunId, RunRecord, refuse_credential_shaped

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
    run_id         text PRIMARY KEY,
    subject        text NOT NULL,
    code_version   text NOT NULL,
    model          text,
    prompt_version text,
    started_at     timestamptz NOT NULL,
    record         jsonb NOT NULL
);

CREATE OR REPLACE FUNCTION eval_runs_refuse_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'eval_runs is append-only: % of run % refused', TG_OP, OLD.run_id;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER eval_runs_append_only
    BEFORE UPDATE OR DELETE ON eval_runs
    FOR EACH ROW EXECUTE FUNCTION eval_runs_refuse_mutation();
"""


def _psycopg2():
    """Imported lazily so the package imports cleanly without the `sql` extra —
    a consumer using only the JSONL store should not inherit a database driver."""
    try:
        import psycopg2
        import psycopg2.errors  # noqa: F401 — reachable as psycopg2.errors below
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "SqlRunStore needs psycopg2 — install with the extra: agent-evals[sql]"
        ) from exc
    return psycopg2


class SqlRunStore:
    """Run records in a Postgres table, oldest first, refused mutation.

    `psycopg2` with `sslmode=require` by default, matching the one existing
    Postgres consumer in the estate rather than introducing a second driver.
    """

    def __init__(self, dsn: str, *, sslmode: str = "require") -> None:
        self.dsn = dsn
        self.sslmode = sslmode
        self._conn = None

    def _connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = _psycopg2().connect(self.dsn, sslmode=self.sslmode)
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()

    def ensure_schema(self) -> None:
        """Idempotent: safe to call on every construction site."""
        conn = self._connection()
        with conn, conn.cursor() as cur:
            cur.execute(_SCHEMA)

    def append(self, record: RunRecord) -> None:
        payload = record.model_dump_json()
        refuse_credential_shaped(payload, run_id=record.run_id, destination="eval_runs")
        version = record.subject_version
        conn = self._connection()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO eval_runs
                        (run_id, subject, code_version, model, prompt_version,
                         started_at, record)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        record.run_id,
                        version.subject,
                        version.code_version,
                        version.model,
                        version.prompt_version,
                        record.started_at,
                        payload,
                    ),
                )
        except _psycopg2().errors.UniqueViolation:
            raise DuplicateRunId(
                f"run id {record.run_id!r} is already in eval_runs — two records "
                "with one id would make `evals report` ambiguous"
            ) from None

    def get(self, run_id: str) -> RunRecord | None:
        conn = self._connection()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT record::text FROM eval_runs WHERE run_id = %s", (run_id,))
            row = cur.fetchone()
        return RunRecord.model_validate_json(row[0]) if row else None

    def all(self) -> list[RunRecord]:
        """Every record, oldest first — the same contract as the JSONL store."""
        conn = self._connection()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT record::text FROM eval_runs ORDER BY started_at, run_id")
            rows = cur.fetchall()
        return [RunRecord.model_validate_json(row[0]) for row in rows]

    def latest(self, subject: str | None = None) -> RunRecord | None:
        """Most recent run, optionally for one subject."""
        sql = "SELECT record::text FROM eval_runs"
        params: tuple = ()
        if subject is not None:
            sql += " WHERE subject = %s"
            params = (subject,)
        sql += " ORDER BY started_at DESC, run_id DESC LIMIT 1"
        conn = self._connection()
        with conn, conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        return RunRecord.model_validate_json(row[0]) if row else None

    def raw(self) -> list[dict]:
        """Every record as a raw dict, oldest first — what `trend.points`
        consumes. The renderer stays source-agnostic; this is its source."""
        conn = self._connection()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT record FROM eval_runs ORDER BY started_at, run_id")
            rows = cur.fetchall()
        return [row[0] for row in rows]
