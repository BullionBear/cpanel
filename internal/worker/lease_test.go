package worker

import (
	"testing"
	"time"
)

func TestLeaseAcquireRenewReleaseTakeOver(t *testing.T) {
	ctx, _, db := testDB(t)
	a := (&Worker{DB: db, Name: "a", Handlers: Handlers{counterKind: nil}, LeaseTTL: time.Minute}).runner()
	b := (&Worker{DB: db, Name: "b", Handlers: Handlers{counterKind: nil}, LeaseTTL: time.Minute}).runner()

	if l, err := a.acquire(ctx); err != nil || l != nil {
		t.Fatalf("acquire with no jobs = %v, %v; want nothing", l, err)
	}
	exec(t, ctx, db, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, not_before) VALUES
			('test.counter', 'later', 'account:2', now() + interval '1 hour'),
			('test.other',   'other', 'account:3', now()),
			('test.counter', 'due',   'account:1', now())`)

	l, err := a.acquire(ctx)
	if err != nil || l == nil || l.key != "account:1" || l.token != 1 {
		t.Fatalf("acquire = %+v, %v; want account:1 with token 1", l, err)
	}
	if l.lost(time.Second) {
		t.Error("a fresh lease counts as lost")
	}
	// account:1 is held, account:2 is not due, account:3 has no job of a kind b runs.
	if l2, err := b.acquire(ctx); err != nil || l2 != nil {
		t.Fatalf("b acquire = %+v, %v; want nothing", l2, err)
	}

	if ok, err := a.renewLease(ctx, l); err != nil || !ok {
		t.Fatalf("renew = %v, %v", ok, err)
	}
	if ok, err := b.renewLease(ctx, l); err != nil || ok {
		t.Errorf("b renewed a's lease: %v, %v", ok, err)
	}

	if err := a.release(ctx, l); err != nil {
		t.Fatal(err)
	}
	if ok, _ := a.renewLease(ctx, l); ok {
		t.Error("renewed a released lease")
	}
	lb, err := b.acquire(ctx)
	if err != nil || lb == nil || lb.key != "account:1" || lb.token != 2 {
		t.Fatalf("b acquire after release = %+v, %v; want account:1 with token 2", lb, err)
	}

	// b claims the job, then dies: its lease runs out with the job still running.
	j, err := b.claim(ctx, lb)
	if err != nil || j == nil || j.LeaseKey != "account:1" || j.Attempt != 0 {
		t.Fatalf("claim = %+v, %v", j, err)
	}
	if again, err := b.claim(ctx, lb); err != nil || again != nil {
		t.Fatalf("second claim = %+v, %v; want nothing", again, err)
	}
	exec(t, ctx, db, `UPDATE ingest.lease SET until = now() - interval '1 second' WHERE key = 'account:1'`)
	if j2, err := b.claim(ctx, lb); err != nil || j2 != nil {
		t.Fatalf("claim under an expired lease = %+v, %v; want nothing", j2, err)
	}
	la, err := a.acquire(ctx)
	if err != nil || la == nil || la.key != "account:1" || la.token != 3 {
		t.Fatalf("a acquire after b's lease ran out = %+v, %v; want account:1 with token 3", la, err)
	}
	var status string
	var attempt int
	if err := db.QueryRow(ctx, `SELECT status, attempt FROM ingest.job WHERE dedup_key = 'due'`).Scan(&status, &attempt); err != nil {
		t.Fatal(err)
	}
	if status != "pending" || attempt != 1 {
		t.Fatalf("b's job after the takeover: %s, attempt %d; want pending, attempt 1", status, attempt)
	}
	// b's late completion is dropped: the job is no longer running under its token.
	if ours, err := b.complete(ctx, lb, j, Result{Requests: 1}, nil); err != nil || ours {
		t.Errorf("b complete after the takeover = %v, %v; want dropped", ours, err)
	}
	if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run`); n != 0 {
		t.Errorf("%d job_run rows, want 0", n)
	}
}

func TestClaimOrder(t *testing.T) {
	ctx, _, db := testDB(t)
	r := (&Worker{DB: db, Name: "a", Handlers: Handlers{counterKind: nil}}).runner()
	exec(t, ctx, db, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, priority, not_before) VALUES
			('test.counter', 'late',     'k', 10,  now() + interval '1 hour'),
			('test.counter', 'other',    'k', 10,  now()),
			('test.other',   'unknown',  'k', 1,   now()),
			('test.counter', 'normal-2', 'k', 100, now() - interval '1 minute'),
			('test.counter', 'normal-1', 'k', 100, now() - interval '2 minutes'),
			('test.counter', 'urgent',   'k', 10,  now() - interval '1 minute')`)
	l, err := r.acquire(ctx)
	if err != nil || l == nil {
		t.Fatal(l, err)
	}
	var got []string
	for {
		j, err := r.claim(ctx, l)
		if err != nil {
			t.Fatal(err)
		}
		if j == nil {
			break
		}
		var key string
		if err := db.QueryRow(ctx, `SELECT dedup_key FROM ingest.job WHERE id = $1`, j.ID).Scan(&key); err != nil {
			t.Fatal(err)
		}
		got = append(got, key)
	}
	want := []string{"urgent", "other", "normal-1", "normal-2"}
	if len(got) != len(want) {
		t.Fatalf("claimed %v, want %v: by priority, then not_before; nothing not yet due or of another kind", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("claimed %v, want %v", got, want)
		}
	}
}
