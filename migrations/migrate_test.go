package migrations

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	"github.com/BullionBear/cpanel/internal/platform/pgtest"
)

func testCtx(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	t.Cleanup(cancel)
	return ctx
}

func connect(t *testing.T, dsn string) *pgx.Conn {
	t.Helper()
	conn, err := pgx.Connect(testCtx(t), dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close(context.Background()) })
	return conn
}

func TestEachMigrationUpDownUp(t *testing.T) {
	srv := pgtest.Start(t)
	ctx := testCtx(t)
	ups, downs, err := load()
	if err != nil {
		t.Fatal(err)
	}
	conn := connect(t, srv.DSN())
	for _, f := range ups {
		if _, err := Up(ctx, srv.DSN()); err != nil {
			t.Fatalf("up %s: %v", f.stem(), err)
		}
		assertSchema(t, ctx, conn, true)
		var n int
		if err := conn.QueryRow(ctx, `SELECT count(*) FROM meta.schema_migrations WHERE version = $1 AND name = $2 AND sha256 = $3`,
			f.version, f.name, f.sha256).Scan(&n); err != nil {
			t.Fatal(err)
		}
		if n != 1 {
			t.Fatalf("version %d recorded %d times", f.version, n)
		}

		if err := ExecSQL(ctx, conn, downs[f.version]); err != nil {
			t.Fatalf("down %s: %v", f.stem(), err)
		}
		// 0001 drops meta, and with it the version table. A later down leaves the table; drop the row
		// so the following up applies the file again.
		if _, err := conn.Exec(ctx, `DELETE FROM meta.schema_migrations WHERE version = $1`, f.version); err != nil {
			var pgErr *pgconn.PgError
			if !errors.As(err, &pgErr) || pgErr.Code != "42P01" { // undefined_table: 0001 down drops meta
				t.Fatal(err)
			}
		}
		assertSchema(t, ctx, conn, false)

		res, err := Up(ctx, srv.DSN())
		if err != nil {
			t.Fatalf("up again %s: %v", f.stem(), err)
		}
		if len(res.Applied) != 1 || res.Applied[0].String() != f.stem() {
			t.Fatalf("reapplied %v, want %s", res.Applied, f.stem())
		}
		assertSchema(t, ctx, conn, true)
	}
}

func TestUpWaitsForTheAdvisoryLock(t *testing.T) {
	srv := pgtest.Start(t)
	ctx := testCtx(t)
	conn := connect(t, srv.DSN())
	if _, err := conn.Exec(ctx, `SELECT pg_advisory_lock(hashtext($1), 1)`, LockName); err != nil {
		t.Fatal(err)
	}

	errc := make(chan error, 1)
	go func() {
		_, err := Up(context.Background(), srv.DSN())
		errc <- err
	}()

	deadline := time.Now().Add(10 * time.Second)
	for {
		var waiting int
		err := conn.QueryRow(ctx, `
			SELECT count(*) FROM pg_locks
			WHERE locktype = 'advisory' AND classid = hashtext($1)::oid AND objid = 1 AND NOT granted`,
			LockName).Scan(&waiting)
		if err != nil {
			t.Fatal(err)
		}
		if waiting == 1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("the second up never waited on the advisory lock")
		}
		time.Sleep(20 * time.Millisecond)
	}
	var rel *string
	if err := conn.QueryRow(ctx, `SELECT to_regclass('meta.audit')::text`).Scan(&rel); err != nil {
		t.Fatal(err)
	}
	if rel != nil {
		t.Fatal("0001 was applied while the advisory lock was held")
	}

	if _, err := conn.Exec(ctx, `SELECT pg_advisory_unlock(hashtext($1), 1)`, LockName); err != nil {
		t.Fatal(err)
	}
	if err := <-errc; err != nil {
		t.Fatal(err)
	}
	assertSchema(t, ctx, conn, true)
}

func TestChangedMigrationIsRefused(t *testing.T) {
	srv := pgtest.Start(t)
	ctx := testCtx(t)
	if _, err := Up(ctx, srv.DSN()); err != nil {
		t.Fatal(err)
	}
	conn := connect(t, srv.DSN())
	if _, err := conn.Exec(ctx, `UPDATE meta.schema_migrations SET sha256 = $1`, strings.Repeat("ab", 32)); err != nil {
		t.Fatal(err)
	}
	res, err := Up(ctx, srv.DSN())
	var drift *DriftError
	if !errors.As(err, &drift) || drift.State != StateChanged || drift.Version != 1 || len(res.Applied) != 0 {
		t.Fatalf("up after a changed file: applied %v, err %v", res.Applied, err)
	}
	rows, err := Status(ctx, srv.DSN())
	if !errors.As(err, &drift) || len(rows) != 1 || rows[0].State != StateChanged {
		t.Fatalf("status: %v %v", rows, err)
	}
}

func TestUpToDateAppliesNothing(t *testing.T) {
	srv := pgtest.Start(t)
	ctx := testCtx(t)
	if _, err := Up(ctx, srv.DSN()); err != nil {
		t.Fatal(err)
	}
	res, err := Up(ctx, srv.DSN())
	if err != nil || len(res.Applied) != 0 {
		t.Fatalf("second up: %v %v", res.Applied, err)
	}
	if err := RequireCurrent(ctx, srv.DSN()); err != nil {
		t.Fatal(err)
	}
}

func TestRequireCurrentWhenBehind(t *testing.T) {
	srv := pgtest.Start(t)
	err := RequireCurrent(testCtx(t), srv.DSN())
	if err == nil || !strings.Contains(err.Error(), "behind") || !strings.Contains(err.Error(), "0001_schema") {
		t.Fatalf("err %v", err)
	}
}

func assertSchema(t *testing.T, ctx context.Context, conn *pgx.Conn, want bool) {
	t.Helper()
	var n int
	err := conn.QueryRow(ctx, `
		SELECT count(*) FROM pg_namespace
		WHERE nspname IN ('pms', 'mirror', 'ingest', 'raw', 'ledger', 'meta')`).Scan(&n)
	if err != nil {
		t.Fatal(err)
	}
	if want && n != 6 {
		t.Fatalf("schemas = %d, want 6", n)
	}
	if !want && n != 0 {
		t.Fatalf("schemas = %d after down, want 0", n)
	}
	if !want {
		return
	}
	var cols int
	err = conn.QueryRow(ctx, `
		SELECT count(*) FROM information_schema.columns
		WHERE table_schema = 'meta' AND table_name = 'audit'
		  AND column_name IN ('id', 'at', 'actor', 'action', 'target', 'params', 'result', 'request_id')`).Scan(&cols)
	if err != nil {
		t.Fatal(err)
	}
	if cols != 8 {
		t.Fatalf("meta.audit columns = %d, want 8", cols)
	}
}
