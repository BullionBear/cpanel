package reconcile

import (
	"context"
	"fmt"
	"slices"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/migrations"
)

var t0 = time.Date(2026, 11, 7, 12, 0, 0, 0, time.UTC)

// testDB starts a throwaway Postgres at the embedded schema version and connects to it.
func testDB(t *testing.T) (context.Context, string, *pgx.Conn) {
	t.Helper()
	srv := pgtest.Start(t)
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	t.Cleanup(cancel)
	if _, err := migrations.Up(ctx, srv.DSN()); err != nil {
		t.Fatal(err)
	}
	conn, err := pgx.Connect(ctx, srv.DSN())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close(context.Background()) })
	return ctx, srv.DSN(), conn
}

func exec(t *testing.T, ctx context.Context, conn *pgx.Conn, sql string, args ...any) {
	t.Helper()
	if _, err := conn.Exec(ctx, sql, args...); err != nil {
		t.Fatalf("%s: %v", sql, err)
	}
}

// run runs rule once in a transaction of its own and commits it.
func run(t *testing.T, ctx context.Context, conn *pgx.Conn, rule Rule, now time.Time) int {
	t.Helper()
	var n int
	err := pgx.BeginFunc(ctx, conn, func(tx pgx.Tx) error {
		var err error
		n, err = rule.Run(ctx, tx, now)
		return err
	})
	if err != nil {
		t.Fatalf("rule %s: %v", rule.Name, err)
	}
	return n
}

// job is the part of an ingest.job row the rules decide.
type job struct {
	Key, Kind, LeaseKey, Status, Params string
	StreamID                            *int64
	Periodic                            *string
	Attempt                             int
}

func jobs(t *testing.T, ctx context.Context, conn *pgx.Conn) map[string]job {
	t.Helper()
	rows, err := conn.Query(ctx, `
		SELECT dedup_key, kind, lease_key, status, params::text, stream_id, periodic_name, attempt FROM ingest.job`)
	if err != nil {
		t.Fatal(err)
	}
	all, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (job, error) {
		var j job
		err := row.Scan(&j.Key, &j.Kind, &j.LeaseKey, &j.Status, &j.Params, &j.StreamID, &j.Periodic, &j.Attempt)
		return j, err
	})
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]job{}
	for _, j := range all {
		out[j.Key] = j
	}
	return out
}

func keys(m map[string]job) []string {
	var out []string
	for k := range m {
		out = append(out, k)
	}
	slices.Sort(out)
	return out
}

func addStream(t *testing.T, ctx context.Context, conn *pgx.Conn, key string, due time.Time, period string, paused bool) int64 {
	t.Helper()
	var reason *string
	var pausedAt *time.Time
	if paused {
		r := "bad_request"
		reason, pausedAt = &r, &due
	}
	var id int64
	err := conn.QueryRow(ctx, `
		INSERT INTO ingest.stream (kind, lease_key, key, source, period, next_due_at, paused_reason, paused_at)
		VALUES ('test.counter', 'account:1', $1, 'system', $2::interval, $3, $4, $5) RETURNING id`,
		key, period, due, reason, pausedAt).Scan(&id)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

func streamKey(id int64) string { return fmt.Sprintf("stream:%d", id) }

func TestStreams(t *testing.T) {
	ctx, _, conn := testDB(t)
	due := addStream(t, ctx, conn, "due", t0.Add(-time.Minute), "2 minutes", false)
	now := addStream(t, ctx, conn, "now", t0, "2 minutes", false)
	addStream(t, ctx, conn, "later", t0.Add(time.Second), "2 minutes", false)
	addStream(t, ctx, conn, "paused", t0.Add(-time.Minute), "2 minutes", true)
	pending := addStream(t, ctx, conn, "pending", t0.Add(-time.Minute), "2 minutes", false)
	running := addStream(t, ctx, conn, "running", t0.Add(-time.Minute), "2 minutes", false)
	exec(t, ctx, conn, `INSERT INTO ingest.job (kind, dedup_key, lease_key, stream_id, attempt) VALUES ('test.counter', $1, 'account:1', $2, 3)`,
		streamKey(pending), pending)
	exec(t, ctx, conn, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, stream_id, status, started_at, worker, lease_token)
		VALUES ('test.counter', $1, 'account:1', $2, 'running', now(), 'w1', 1)`, streamKey(running), running)

	if n := run(t, ctx, conn, Streams, t0); n != 2 {
		t.Fatalf("inserted %d, want 2: the overdue stream and the one due right now", n)
	}
	got := jobs(t, ctx, conn)
	want := []string{streamKey(due), streamKey(now), streamKey(pending), streamKey(running)}
	slices.Sort(want)
	if !slices.Equal(keys(got), want) {
		t.Fatalf("jobs %v, want %v: none for the later or paused stream, no second one for a busy stream", keys(got), want)
	}
	for _, id := range []int64{due, now} {
		j := got[streamKey(id)]
		if j.Kind != "test.counter" || j.LeaseKey != "account:1" || j.Status != "pending" || j.Attempt != 0 ||
			j.Params != "{}" || j.StreamID == nil || *j.StreamID != id || j.Periodic != nil {
			t.Errorf("job %+v", j)
		}
	}
	if j := got[streamKey(pending)]; j.Attempt != 3 {
		t.Errorf("the existing job was touched: %+v", j)
	}
	if n := run(t, ctx, conn, Streams, t0); n != 0 {
		t.Errorf("second run inserted %d, want 0", n)
	}
}

func TestPeriodics(t *testing.T) {
	ctx, _, conn := testDB(t)
	for _, p := range []struct {
		name    string
		due     time.Time
		enabled bool
	}{
		{"due", t0.Add(-time.Minute), true},
		{"off", t0.Add(-time.Minute), false},
		{"later", t0.Add(time.Second), true},
		{"busy", t0.Add(-time.Minute), true},
	} {
		exec(t, ctx, conn, `
			INSERT INTO ingest.periodic (name, kind, lease_key, period, params, enabled, next_due_at)
			VALUES ($1, 'test.' || $1, 'global:test', '10 minutes', '{"n": 1}', $2, $3)`, p.name, p.enabled, p.due)
	}
	exec(t, ctx, conn, `INSERT INTO ingest.job (kind, dedup_key, lease_key, periodic_name) VALUES ('test.busy', 'periodic:busy', 'global:test', 'busy')`)

	if n := run(t, ctx, conn, Periodics, t0); n != 1 {
		t.Fatalf("inserted %d, want 1", n)
	}
	got := jobs(t, ctx, conn)
	if want := []string{"periodic:busy", "periodic:due"}; !slices.Equal(keys(got), want) {
		t.Fatalf("jobs %v, want %v", keys(got), want)
	}
	j := got["periodic:due"]
	if j.Kind != "test.due" || j.LeaseKey != "global:test" || j.Status != "pending" || j.Params != `{"n": 1}` ||
		j.Periodic == nil || *j.Periodic != "due" || j.StreamID != nil {
		t.Errorf("job %+v", j)
	}

	next := map[string]time.Time{}
	rows, err := conn.Query(ctx, `SELECT name, next_due_at FROM ingest.periodic`)
	if err != nil {
		t.Fatal(err)
	}
	var name string
	var at time.Time
	if _, err := pgx.ForEachRow(rows, []any{&name, &at}, func() error { next[name] = at.UTC(); return nil }); err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]time.Time{
		"due":   t0.Add(10 * time.Minute), // counted from dispatch
		"off":   t0.Add(-time.Minute),
		"later": t0.Add(time.Second),
		"busy":  t0.Add(-time.Minute), // still due: it goes out once the unfinished job ends
	} {
		if !next[name].Equal(want) {
			t.Errorf("%s next_due_at = %s, want %s", name, next[name], want)
		}
	}
	if n := run(t, ctx, conn, Periodics, t0); n != 0 {
		t.Errorf("second run inserted %d, want 0", n)
	}
	if n := run(t, ctx, conn, Periodics, t0.Add(5*time.Minute)); n != 1 {
		t.Errorf("run at +5m inserted %d, want 1: later is due by then", n)
	}
}

func TestSingletonRegistersOnce(t *testing.T) {
	ctx, _, conn := testDB(t)
	if n := run(t, ctx, conn, MaintPartitions, t0); n != 1 {
		t.Fatalf("inserted %d, want 1", n)
	}
	row := func() string {
		t.Helper()
		var s string
		err := conn.QueryRow(ctx, `
			SELECT concat_ws(' ', kind, lease_key, period, params, enabled, next_due_at AT TIME ZONE 'UTC')
			FROM ingest.periodic WHERE name = 'maint.partitions'`).Scan(&s)
		if err != nil {
			t.Fatal(err)
		}
		return s
	}
	if got, want := row(), "maint.partitions global:maint 01:00:00 {} t 2026-11-07 12:00:00"; got != want {
		t.Fatalf("row %q, want %q", got, want)
	}

	// The operator's changes stay.
	exec(t, ctx, conn, `UPDATE ingest.periodic SET period = '2 hours', enabled = false WHERE name = 'maint.partitions'`)
	if n := run(t, ctx, conn, MaintPartitions, t0.Add(time.Hour)); n != 0 {
		t.Errorf("second run inserted %d, want 0", n)
	}
	if got, want := row(), "maint.partitions global:maint 02:00:00 {} f 2026-11-07 12:00:00"; got != want {
		t.Errorf("row %q, want %q", got, want)
	}
}

func TestCredentialsDispatchesUnverified(t *testing.T) {
	ctx, _, conn := testDB(t)
	var live, deleted int64
	if err := conn.QueryRow(ctx, `
		INSERT INTO pms.exchange_account (exchange, exch_account_uid, label)
		VALUES ('binance', 'live', 'live') RETURNING id`).Scan(&live); err != nil {
		t.Fatal(err)
	}
	if err := conn.QueryRow(ctx, `
		INSERT INTO pms.exchange_account (exchange, exch_account_uid, label, deleted_at)
		VALUES ('binance', 'gone', 'gone', now()) RETURNING id`).Scan(&deleted); err != nil {
		t.Fatal(err)
	}
	insert := func(account int64, status string) {
		t.Helper()
		exec(t, ctx, conn, `
			INSERT INTO pms.credential (exchange_account_id, purpose, status, ciphertext, api_key_last4)
			VALUES ($1, 'read', $2, '\x01', 'key9')`, account, status)
	}
	insert(live, "unverified")
	insert(live, "active")
	insert(deleted, "unverified")
	if n := run(t, ctx, conn, Credentials, t0); n != 1 {
		t.Fatalf("inserted %d, want 1", n)
	}
	var kind, dedup, lease string
	if err := conn.QueryRow(ctx, `SELECT kind, dedup_key, lease_key FROM ingest.job`).Scan(&kind, &dedup, &lease); err != nil {
		t.Fatal(err)
	}
	if kind != CredentialVerifyKind || lease != AccountLease(live) || dedup == "" {
		t.Fatalf("job %s %s %s", kind, dedup, lease)
	}
	if n := run(t, ctx, conn, Credentials, t0); n != 0 {
		t.Fatalf("second run inserted %d, want 0", n)
	}
}
