package migrations

import (
	"errors"
	"testing"

	"github.com/jackc/pgx/v5/pgconn"

	"github.com/BullionBear/cpanel/internal/platform/pgtest"
)

func TestStreamSourceRenamesScopeAndDropsDiscovered(t *testing.T) {
	srv := pgtest.Start(t)
	ctx := testCtx(t)
	conn := connect(t, srv.DSN())
	if _, err := Up(ctx, srv.DSN()); err != nil {
		t.Fatal(err)
	}
	_, downs, err := load()
	if err != nil {
		t.Fatal(err)
	}
	if err := ExecSQL(ctx, conn, downs[14]); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Exec(ctx, `DELETE FROM meta.schema_migrations WHERE version = 14`); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Exec(ctx, `
		INSERT INTO ingest.stream (kind, lease_key, key, source, period)
		VALUES ('trade', 'account:1', 'scope', 'scope', '1 hour'),
		       ('trade', 'account:1', 'found', 'discovered', '1 hour'),
		       ('trade', 'account:1', 'kept', 'manual', '1 hour')`); err != nil {
		t.Fatal(err)
	}
	ups, _, err := load()
	if err != nil {
		t.Fatal(err)
	}
	var up14 string
	for _, f := range ups {
		if f.version == 14 {
			up14 = string(f.body)
		}
	}
	if up14 == "" {
		t.Fatal("0014 up file missing")
	}
	if err := ExecSQL(ctx, conn, up14); err != nil {
		t.Fatal(err)
	}
	var mftiko, manual, discovered int
	if err := conn.QueryRow(ctx, `
		SELECT count(*) FILTER (WHERE source = 'mftiko' AND key = 'scope'),
		       count(*) FILTER (WHERE source = 'manual' AND key = 'kept'),
		       count(*) FILTER (WHERE source = 'discovered')
		FROM ingest.stream`).Scan(&mftiko, &manual, &discovered); err != nil {
		t.Fatal(err)
	}
	if mftiko != 1 || manual != 1 || discovered != 0 {
		t.Fatalf("mftiko %d manual %d discovered %d", mftiko, manual, discovered)
	}
	_, err = conn.Exec(ctx, `
		INSERT INTO ingest.stream (kind, lease_key, key, source, period)
		VALUES ('trade', 'account:1', 'again', 'discovered', '1 hour')`)
	var pg *pgconn.PgError
	if err == nil || !errors.As(err, &pg) || pg.Code != "23514" {
		t.Fatalf("discovered insert err = %v, want check_violation", err)
	}

	if err := ExecSQL(ctx, conn, downs[14]); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Exec(ctx, `
		INSERT INTO ingest.stream (kind, lease_key, key, source, period)
		VALUES ('trade', 'account:1', 'again', 'discovered', '1 hour')`); err != nil {
		t.Fatalf("down did not restore discovered: %v", err)
	}
	var back string
	if err := conn.QueryRow(ctx, `SELECT source FROM ingest.stream WHERE key = 'scope'`).Scan(&back); err != nil {
		t.Fatal(err)
	}
	if back != "scope" {
		t.Fatalf("source after down = %s, want scope", back)
	}
}
