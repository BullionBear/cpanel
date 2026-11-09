package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/rs/zerolog"

	"github.com/BullionBear/cpanel/internal/auth"
	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/migrations"
)

func TestOps(t *testing.T) {
	db := pgtest.Start(t)
	ctx := context.Background()
	if _, err := migrations.Up(ctx, db.DSN()); err != nil {
		t.Fatal(err)
	}
	pool := poolFor(t, db.DSN())
	h := Handler(Config{Log: zerolog.Nop(), Pool: pool, Auth: testAuthSettings()})
	raw := "cpanel_ops_test"
	token, err := (&auth.Store{Pool: pool}).CreateToken(ctx, "42", "ada", "ops", raw)
	if err != nil {
		t.Fatal(err)
	}
	authz := bearerHeader(raw)

	backing, paused, healthy := seedOps(t, ctx, pool)

	t.Run("unauthenticated", func(t *testing.T) {
		problemOf(t, call(h, http.MethodGet, "/streams", "", nil), http.StatusUnauthorized, "unauthenticated")
		problemOf(t, call(h, http.MethodPost, "/streams/1/resume", "", nil), http.StatusUnauthorized, "unauthenticated")
		problemOf(t, call(h, http.MethodGet, "/mirror/status", "", nil), http.StatusUnauthorized, "unauthenticated")
	})

	t.Run("filters", func(t *testing.T) {
		streams := getJSON[[]streamJSON](t, h, "/streams?lease_key=account:1", authz)
		if len(streams) != 2 {
			t.Fatalf("lease key %d", len(streams))
		}
		got := getJSON[[]streamJSON](t, h, "/streams?status=paused", authz)
		if len(got) != 1 || got[0].ID != paused || got[0].Status != "paused" {
			t.Fatalf("%+v", got)
		}
		got = getJSON[[]streamJSON](t, h, "/streams?error_class=unavailable", authz)
		if len(got) != 1 || got[0].ID != backing || got[0].Status != "backing_off" || got[0].ConsecutiveFailures != 3 {
			t.Fatalf("%+v", got)
		}
		got = getJSON[[]streamJSON](t, h, "/streams?kind=trade&status=ok", authz)
		if len(got) != 1 || got[0].ID != healthy {
			t.Fatalf("%+v", got)
		}
		jobs := getJSON[[]jobJSON](t, h, "/jobs?lease_key=account:1&status=pending&error_class=unavailable", authz)
		if len(jobs) != 1 || jobs[0].StreamID == nil || *jobs[0].StreamID != backing {
			t.Fatalf("%+v", jobs)
		}
		running := getJSON[[]jobJSON](t, h, "/jobs?status=running", authz)
		if len(running) != 1 || running[0].LeaseKey != "account:2" {
			t.Fatalf("%+v", running)
		}
	})

	t.Run("job run pages", func(t *testing.T) {
		seen := map[int64]bool{}
		var previous time.Time
		path := "/job-runs?lease_key=account:1&limit=1"
		pages := 0
		for {
			page := getJSON[runPage](t, h, path, authz)
			if len(page.Runs) != 1 {
				t.Fatalf("page %+v", page)
			}
			run := page.Runs[0]
			if seen[run.ID] {
				t.Fatalf("duplicate %d", run.ID)
			}
			seen[run.ID] = true
			if pages > 0 && !run.FinishedAt.Before(previous) {
				t.Fatalf("order %s then %s", previous, run.FinishedAt)
			}
			previous = run.FinishedAt
			pages++
			if page.NextCursor == nil {
				break
			}
			if pages > 5 {
				t.Fatal("cursor did not end")
			}
			path = "/job-runs?lease_key=account:1&limit=1&cursor=" + url.QueryEscape(*page.NextCursor)
		}
		if pages != 3 {
			t.Fatalf("pages %d ids %v", pages, seen)
		}
		failed := getJSON[runPage](t, h, "/job-runs?error_class=unavailable&status=failed", authz)
		if len(failed.Runs) != 1 || failed.Runs[0].ErrorClass == nil || *failed.Runs[0].ErrorClass != "unavailable" {
			t.Fatalf("%+v", failed)
		}
		from := time.Now().UTC().Add(-30 * time.Minute).Format(time.RFC3339)
		recent := getJSON[runPage](t, h, "/job-runs?from="+url.QueryEscape(from), authz)
		if len(recent.Runs) != 1 {
			t.Fatalf("from %s got %d", from, len(recent.Runs))
		}
	})

	t.Run("mirror status", func(t *testing.T) {
		empty := getJSON[mirrorStatus](t, h, "/mirror/status", authz)
		if len(empty.Sources) != 0 {
			t.Fatalf("empty %+v", empty)
		}
		if _, err := pool.Exec(ctx, `
			INSERT INTO mirror.sync_state (source, last_attempt_at, last_ok_at, last_complete_at, items)
			VALUES ('mftiko.accounts', now() - interval '1 minute', now(), now(), 4)`); err != nil {
			t.Fatal(err)
		}
		if _, err := pool.Exec(ctx, `
			INSERT INTO mirror.sync_state (source, last_ok_at, items, revision, remote_revision)
			VALUES ('sym:BinanceUM:Perp', now(), 2, 6, 6)`); err != nil {
			t.Fatal(err)
		}
		if _, err := pool.Exec(ctx, `
			INSERT INTO ingest.job_run (
				id, kind, dedup_key, lease_key, periodic_name, priority, attempt, params, enqueued_at, finished_at, status, error_class, error)
			VALUES (900, 'mftiko.sync', 'periodic:mftiko.sync', 'global:mftiko', 'mftiko.sync', 100, 1, '{}',
			        now() - interval '1 minute', now(), 'failed', 'unavailable', 'mftiko is down')`); err != nil {
			t.Fatal(err)
		}
		got := getJSON[mirrorStatus](t, h, "/mirror/status", authz)
		if len(got.Sources) != 2 || got.Sources[0].Source != "mftiko.accounts" || got.Sources[0].Items == nil || *got.Sources[0].Items != 4 {
			t.Fatalf("%+v", got)
		}
		if got.Sources[1].Source != "sym:BinanceUM:Perp" || got.Sources[1].Revision == nil || *got.Sources[1].Revision != 6 || got.Sources[1].Remote == nil || *got.Sources[1].Remote != 6 {
			t.Fatalf("sym %+v", got.Sources[1])
		}
		src := got.Sources[0]
		if src.LastOK == nil || src.LastErrorClass == nil || *src.LastErrorClass != "unavailable" || src.LastError == nil || *src.LastError != "mftiko is down" {
			t.Fatalf("%+v", src)
		}
		if src.Regions == nil {
			t.Fatal("unavailable_regions is null")
		}
		if len(got.Unresolved) != 0 {
			t.Fatalf("unresolved %+v", got.Unresolved)
		}
		if _, err := pool.Exec(ctx, `
			INSERT INTO mirror.uid_lookup (uid, first_tried_at, last_tried_at, attempts, last_result)
			VALUES (42, now(), now(), 3, 'not_found')`); err != nil {
			t.Fatal(err)
		}
		got = getJSON[mirrorStatus](t, h, "/mirror/status", authz)
		if len(got.Unresolved) != 1 || got.Unresolved[0].UID != 42 || got.Unresolved[0].Attempts != 3 || got.Unresolved[0].Result != "not_found" {
			t.Fatalf("unresolved %+v", got.Unresolved)
		}
	})

	t.Run("bad cursor", func(t *testing.T) {
		problemOf(t, call(h, http.MethodGet, "/job-runs?cursor=nope", "", authz), http.StatusBadRequest, "bad_request")
	})

	t.Run("resume writes audit", func(t *testing.T) {
		body := getJSON[streamJSON](t, h, "/streams/"+strconv.FormatInt(paused, 10)+"/resume", authz)
		if body.Status != "ok" || body.PausedReason != nil {
			t.Fatalf("%+v", body)
		}
		var reason *string
		var due bool
		if err := pool.QueryRow(ctx, `
			SELECT paused_reason, paused_at IS NULL AND next_due_at > now() - interval '1 minute'
			FROM ingest.stream WHERE id = $1`, paused).Scan(&reason, &due); err != nil {
			t.Fatal(err)
		}
		if reason != nil || !due {
			t.Fatalf("reason %v due %v", reason, due)
		}
		var actor, action, target, pausedReason string
		if err := pool.QueryRow(ctx, `
			SELECT actor, action, target, params->>'paused_reason'
			FROM meta.audit WHERE action = 'stream.resume' AND target = $1`, "stream:"+strconv.FormatInt(paused, 10)).
			Scan(&actor, &action, &target, &pausedReason); err != nil {
			t.Fatal(err)
		}
		if actor != "token:"+token.ID || action != "stream.resume" || pausedReason != "bad_request" {
			t.Fatalf("%s %s %s %s", actor, action, target, pausedReason)
		}
		again := getJSON[streamJSON](t, h, "/streams/"+strconv.FormatInt(paused, 10)+"/resume", authz)
		if again.Status != "ok" {
			t.Fatalf("%+v", again)
		}
		var n int
		if err := pool.QueryRow(ctx, `SELECT count(*) FROM meta.audit WHERE action = 'stream.resume'`).Scan(&n); err != nil {
			t.Fatal(err)
		}
		if n != 1 {
			t.Fatalf("audit rows %d", n)
		}
	})

	t.Run("resume missing", func(t *testing.T) {
		problemOf(t, call(h, http.MethodPost, "/streams/999999/resume", "", authz), http.StatusNotFound, "not_found")
	})

	t.Run("session origin", func(t *testing.T) {
		var id int64
		if err := pool.QueryRow(ctx, `
			INSERT INTO ingest.stream (kind, lease_key, key, source, period, paused_reason, paused_at)
			VALUES ('trade', 'account:9', 'paused-2', 'manual', interval '1 hour', 'bad_request', now())
			RETURNING id`).Scan(&id); err != nil {
			t.Fatal(err)
		}
		secret, err := auth.NewSecret(16)
		if err != nil {
			t.Fatal(err)
		}
		if err := (&auth.Store{Pool: pool}).CreateSession(ctx, secret, auth.User{ID: "7", Username: "ada", DisplayName: "Ada"}, time.Hour); err != nil {
			t.Fatal(err)
		}
		cookie := cookieHeader(secret)
		problemOf(t, call(h, http.MethodPost, "/streams/"+strconv.FormatInt(id, 10)+"/resume", "", cookie), http.StatusForbidden, "forbidden")
		ok := getJSON[streamJSON](t, h, "/streams/"+strconv.FormatInt(id, 10)+"/resume", mergeHeaders(cookie, originHeader()))
		if ok.ID != id || ok.Status != "ok" {
			t.Fatalf("%+v", ok)
		}
		var actor string
		if err := pool.QueryRow(ctx, `
			SELECT actor FROM meta.audit WHERE action = 'stream.resume' AND target = $1`, "stream:"+strconv.FormatInt(id, 10)).
			Scan(&actor); err != nil {
			t.Fatal(err)
		}
		if actor != "discord:7" {
			t.Fatalf("actor %s", actor)
		}
	})
}

func seedOps(t *testing.T, ctx context.Context, pool *pgxpool.Pool) (backing, paused, healthy int64) {
	t.Helper()
	if err := pool.QueryRow(ctx, `
		INSERT INTO ingest.stream (kind, lease_key, key, source, period, last_ok_at, consecutive_failures, last_error_class, last_error)
		VALUES ('trade', 'account:1', 'a', 'manual', interval '1 hour', now() - interval '30 minutes', 3, 'unavailable', 'down')
		RETURNING id`).Scan(&backing); err != nil {
		t.Fatal(err)
	}
	if err := pool.QueryRow(ctx, `
		INSERT INTO ingest.stream (kind, lease_key, key, source, period, paused_reason, paused_at)
		VALUES ('funding', 'account:2', 'b', 'manual', interval '1 hour', 'bad_request', now())
		RETURNING id`).Scan(&paused); err != nil {
		t.Fatal(err)
	}
	if err := pool.QueryRow(ctx, `
		INSERT INTO ingest.stream (kind, lease_key, key, source, period, last_ok_at)
		VALUES ('trade', 'account:1', 'c', 'manual', interval '1 hour', now())
		RETURNING id`).Scan(&healthy); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, stream_id, status)
		VALUES ('trade', 'stream:backing', 'account:1', $1, 'pending')`, backing); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, stream_id, status, attempt, started_at, worker, lease_token)
		VALUES ('funding', 'stream:paused', 'account:2', $1, 'running', 1, now(), 'worker-a', 1)`, paused); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	for i, at := range []time.Time{now, now.Add(-time.Hour), now.Add(-2 * time.Hour)} {
		status, class := "succeeded", (*string)(nil)
		if i == 1 {
			status = "failed"
			v := "unavailable"
			class = &v
		}
		if _, err := pool.Exec(ctx, `
			INSERT INTO ingest.job_run (
				id, kind, dedup_key, lease_key, stream_id, priority, attempt, params,
				enqueued_at, finished_at, status, error_class, error)
			VALUES ($1, 'trade', $2, 'account:1', $3, 100, 1, '{}', $4, $4, $5, $6, $7)`,
			int64(i+1), "stream:run:"+strconv.Itoa(i), backing, at, status, class, class); err != nil {
			t.Fatal(err)
		}
	}
	return backing, paused, healthy
}

type streamJSON struct {
	ID                  int64   `json:"id"`
	Status              string  `json:"status"`
	PausedReason        *string `json:"paused_reason"`
	ConsecutiveFailures int     `json:"consecutive_failures"`
	LeaseKey            string  `json:"lease_key"`
}

type jobJSON struct {
	ID       int64  `json:"id"`
	LeaseKey string `json:"lease_key"`
	Status   string `json:"status"`
	StreamID *int64 `json:"stream_id"`
}

type runJSON struct {
	ID         int64     `json:"id"`
	FinishedAt time.Time `json:"finished_at"`
	ErrorClass *string   `json:"error_class"`
	Status     string    `json:"status"`
}

type runPage struct {
	Runs       []runJSON `json:"runs"`
	NextCursor *string   `json:"next_cursor"`
}

type mirrorStatus struct {
	Sources []struct {
		Source         string   `json:"source"`
		Items          *int64   `json:"items"`
		LastOK         *string  `json:"last_ok_at"`
		LastErrorClass *string  `json:"last_error_class"`
		LastError      *string  `json:"last_error"`
		Regions        []string `json:"unavailable_regions"`
		Revision       *int64   `json:"revision"`
		Remote         *int64   `json:"remote_revision"`
	} `json:"sources"`
	Unresolved []struct {
		UID      int64  `json:"uid"`
		Attempts int64  `json:"attempts"`
		Result   string `json:"last_result"`
	} `json:"unresolved_uids"`
}

func getJSON[T any](t *testing.T, h http.Handler, path string, header http.Header) T {
	t.Helper()
	method := http.MethodGet
	if strings.Contains(strings.Split(path, "?")[0], "/resume") {
		method = http.MethodPost
	}
	rec := call(h, method, path, "", header)
	if rec.Code != http.StatusOK {
		t.Fatalf("%s %d %s", path, rec.Code, rec.Body)
	}
	var out T
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	return out
}
