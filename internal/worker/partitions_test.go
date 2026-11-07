package worker

import (
	"slices"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func TestMaintPartitions(t *testing.T) {
	ctx, _, db := testDB(t)
	var today time.Time
	if err := db.QueryRow(ctx, `SELECT (now() AT TIME ZONE 'UTC')::date`).Scan(&today); err != nil {
		t.Fatal(err)
	}
	day := func(d int) time.Time { return today.AddDate(0, 0, d).Add(12 * time.Hour) }
	insertRun := `
		INSERT INTO ingest.job_run (id, kind, dedup_key, lease_key, priority, attempt, params, enqueued_at, finished_at, status, error_class)
		VALUES ($1, 'k', 'd', 'l', 1, 0, '{}', $2, $2, $3, $4)`

	// A partition 40 days old with a failure in it, and old runs in the default partition.
	exec(t, ctx, db, `SELECT ingest.ensure_job_run_partition($1::date)`, today.AddDate(0, 0, -40))
	exec(t, ctx, db, insertRun, 1, day(-40), "failed", "auth")
	exec(t, ctx, db, insertRun, 2, day(-40), "succeeded", nil)
	exec(t, ctx, db, insertRun, 3, day(-35), "failed", "unavailable")
	exec(t, ctx, db, insertRun, 4, day(-5), "failed", "internal") // within 30 days: stays in job_run
	exec(t, ctx, db, `
		INSERT INTO ingest.job_failure (id, kind, dedup_key, lease_key, priority, attempt, params, enqueued_at, finished_at, status, error_class)
		VALUES (10, 'k', 'd', 'l', 1, 0, '{}', $1, $1, 'failed', 'auth'), (11, 'k', 'd', 'l', 1, 0, '{}', $2, $2, 'failed', 'auth')`,
		today.AddDate(-1, 0, -2), today.AddDate(0, -6, 0))
	exec(t, ctx, db, `INSERT INTO ingest.job (kind, dedup_key, lease_key) VALUES ('maint.partitions', 'periodic:x', 'global:maint')`)

	stop := start(t, &Worker{DB: db, Name: "w1", Handlers: Handlers{"maint.partitions": MaintPartitions}, Slots: 1, Poll: 20 * time.Millisecond})
	waitFor(t, 10*time.Second, "maint.partitions run", func() bool {
		return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE kind = 'maint.partitions'`) == 1
	})
	stop()

	var status string
	var items int
	if err := db.QueryRow(ctx, `SELECT status, items FROM ingest.job_run WHERE kind = 'maint.partitions'`).Scan(&status, &items); err != nil {
		t.Fatal(err)
	}
	if status != "succeeded" || items != 8+1+1 {
		t.Errorf("maint.partitions run: %s, %d items; want succeeded, 8 made + 1 dropped + 1 failure deleted", status, items)
	}
	rows, err := db.Query(ctx, `
		SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
		WHERE i.inhparent = 'ingest.job_run'::regclass ORDER BY c.relname`)
	if err != nil {
		t.Fatal(err)
	}
	parts, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"job_run_default"}
	for d := range 8 {
		want = append(want, "job_run_"+today.AddDate(0, 0, d).Format("20060102"))
	}
	slices.Sort(want)
	if !slices.Equal(parts, want) {
		t.Errorf("partitions %v, want %v: today and 7 days ahead, the 40-day-old one dropped", parts, want)
	}
	rows, err = db.Query(ctx, `SELECT id FROM ingest.job_failure ORDER BY id`)
	if err != nil {
		t.Fatal(err)
	}
	failures, err := pgx.CollectRows(rows, pgx.RowTo[int])
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(failures, []int{1, 3, 11}) {
		t.Errorf("job_failure ids %v, want [1 3 11]: the expired failures kept, the one older than a year deleted", failures)
	}
	rows, err = db.Query(ctx, `SELECT id FROM ingest.job_run WHERE kind = 'k' ORDER BY id`)
	if err != nil {
		t.Fatal(err)
	}
	left, err := pgx.CollectRows(rows, pgx.RowTo[int])
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(left, []int{4}) {
		t.Errorf("job_run ids %v, want [4]", left)
	}
}
