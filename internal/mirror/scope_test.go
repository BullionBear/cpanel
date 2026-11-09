package mirror

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/mftiko"
	"github.com/BullionBear/cpanel/internal/reconcile"
	"github.com/BullionBear/cpanel/internal/worker"
)

func TestScopeGrowsAndSinceOnlyMovesEarlier(t *testing.T) {
	ctx, db := testDB(t)
	var mu sync.Mutex
	type reply struct {
		status int
		body   string
	}
	var queue []reply
	var queries []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method %s", r.Method)
		}
		w.Header().Set("Content-Type", "application/json")
		if !strings.Contains(r.URL.Path, "/scopes") {
			io.WriteString(w, `{"freshness":{"unavailable_regions":[]},"items":[
				{"api_id":9,"name":"pm","kind":"BinancePM","exchange":"binance","exch_account_uid":"u9","revision":1,"referenced_by":[],"settings":{}}
			]}`)
			return
		}
		if r.URL.Query().Get("api_id") != "" || r.URL.Query().Get("limit") != "1000" {
			t.Errorf("query %s", r.URL.RawQuery)
		}
		mu.Lock()
		queries = append(queries, r.URL.RawQuery)
		if len(queue) == 0 {
			mu.Unlock()
			http.Error(w, "no script", http.StatusInternalServerError)
			return
		}
		next := queue[0]
		queue = queue[1:]
		mu.Unlock()
		if next.status != http.StatusOK {
			http.Error(w, "down", next.status)
			return
		}
		io.WriteString(w, next.body)
	}))
	t.Cleanup(srv.Close)
	push := func(parts ...reply) {
		t.Helper()
		mu.Lock()
		queue = append(queue, parts...)
		mu.Unlock()
	}
	take := func() []string {
		t.Helper()
		mu.Lock()
		defer mu.Unlock()
		out := append([]string(nil), queries...)
		queries = nil
		return out
	}

	client, err := mftiko.New(srv.URL, "tok", srv.Client())
	if err != nil {
		t.Fatal(err)
	}
	enqueue(t, ctx, db)
	startWorker(t, &worker.Worker{
		DB: db, Name: "w", Slots: 1, Poll: 20 * time.Millisecond, BudgetTime: time.Minute,
		Handlers: worker.Handlers{reconcile.MftikoSync.Name: MftikoSync(client)},
		Local:    []string{reconcile.MftikoSync.Name},
	})

	truncated := time.Date(2026, 11, 7, 1, 2, 3, 123456000, time.UTC)
	push(
		reply{http.StatusOK, `{"freshness":{"unavailable_regions":[]},"next_cursor":"s2","items":[
			{"api_id":9,"uid":1,"since":"2026-11-07T01:02:03.123456789Z"},
			{"api_id":9,"uid":2,"since":"2026-11-07T00:00:00Z"}
		]}`},
		reply{http.StatusOK, `{"freshness":{"unavailable_regions":[]},"items":[
			{"api_id":11,"uid":1,"since":"2026-11-07T02:00:00Z"}
		]}`},
	)
	if status, class := waitJob(t, ctx, db, 1); status != "succeeded" || class != "" {
		t.Fatalf("first run %s %s", status, class)
	}
	got := scopeRows(t, ctx, db)
	if len(got) != 3 {
		t.Fatalf("%d scopes", len(got))
	}
	if !got[key(9, 1)].Since.Equal(truncated) || got[key(9, 1)].Lowered != nil {
		t.Fatalf("truncated %+v", got[key(9, 1)])
	}
	if !got[key(9, 2)].Since.Equal(time.Date(2026, 11, 7, 0, 0, 0, 0, time.UTC)) {
		t.Fatalf("uid 2 %+v", got[key(9, 2)])
	}
	if _, ok := got[key(11, 1)]; !ok {
		t.Fatal("scope for an api_id outside the account page was dropped")
	}
	accountsGot := accounts(t, ctx, db)
	if accountsGot[9].Kind != "BinancePM" {
		t.Fatalf("account %+v", accountsGot[9])
	}
	items, complete, regions := scopeState(t, ctx, db)
	if items != 3 || complete == nil || len(regions) != 0 {
		t.Fatalf("state items %d complete %v regions %v", items, complete, regions)
	}
	firstQueries := take()
	if len(firstQueries) != 2 || strings.Contains(firstQueries[0], "cursor=") || !strings.Contains(firstQueries[1], "cursor=s2") {
		t.Fatalf("queries %v", firstQueries)
	}
	opened1 := got[key(9, 1)].First
	opened2 := got[key(9, 2)].First
	untouched := got[key(11, 1)].Last

	// A later since stays. An earlier one replaces it and stamps since_lowered_at.
	push(reply{http.StatusOK, `{"freshness":{"unavailable_regions":[]},"items":[
		{"api_id":9,"uid":1,"since":"2026-11-08T00:00:00Z"},
		{"api_id":9,"uid":2,"since":"2026-11-06T00:00:00.0000009Z"}
	]}`})
	enqueue(t, ctx, db)
	if status, class := waitJob(t, ctx, db, 2); status != "succeeded" || class != "" {
		t.Fatalf("second run %s %s", status, class)
	}
	got = scopeRows(t, ctx, db)
	if !got[key(9, 1)].Since.Equal(truncated) || got[key(9, 1)].Lowered != nil || !got[key(9, 1)].First.Equal(opened1) {
		t.Fatalf("later since %+v", got[key(9, 1)])
	}
	earlier := time.Date(2026, 11, 6, 0, 0, 0, 0, time.UTC)
	if !got[key(9, 2)].Since.Equal(earlier) || got[key(9, 2)].Lowered == nil || !got[key(9, 2)].First.Equal(opened2) {
		t.Fatalf("earlier since %+v", got[key(9, 2)])
	}
	if !got[key(11, 1)].Last.Equal(untouched) {
		t.Fatal("untouched row moved")
	}
	held := scopeRows(t, ctx, db)

	// An empty, complete response does not delete or touch rows.
	push(reply{http.StatusOK, `{"freshness":{"unavailable_regions":[]},"items":[]}`})
	enqueue(t, ctx, db)
	if status, class := waitJob(t, ctx, db, 3); status != "succeeded" || class != "" {
		t.Fatalf("empty run %s %s", status, class)
	}
	if diff := scopeDiff(held, scopeRows(t, ctx, db)); diff != "" {
		t.Fatal(diff)
	}

	// A partial page inserts what it saw and leaves the rest. last_complete_at stays.
	_, frozen, _ := scopeState(t, ctx, db)
	push(reply{http.StatusOK, `{"freshness":{"unavailable_regions":["eu","ap","eu"]},"items":[
		{"api_id":12,"uid":4,"since":"2026-11-07T03:00:00Z"}
	]}`})
	enqueue(t, ctx, db)
	if status, class := waitJob(t, ctx, db, 4); status != "succeeded" || class != "" {
		t.Fatalf("partial run %s %s", status, class)
	}
	got = scopeRows(t, ctx, db)
	if _, ok := got[key(12, 4)]; !ok || len(got) != 4 {
		t.Fatalf("partial rows %d", len(got))
	}
	for _, id := range [][2]int64{key(9, 1), key(9, 2), key(11, 1)} {
		if !got[id].Since.Equal(held[id].Since) || !got[id].Last.Equal(held[id].Last) {
			t.Fatalf("missing scope changed %+v", got[id])
		}
	}
	items, complete, regions = scopeState(t, ctx, db)
	if items != 4 || complete == nil || !complete.Equal(*frozen) || len(regions) != 2 || regions[0] != "ap" || regions[1] != "eu" {
		t.Fatalf("partial state items %d complete %v frozen %v regions %v", items, complete, frozen, regions)
	}

	// Page 2 fails. Page 1 stays committed. The next round starts at the first page.
	take()
	push(
		reply{http.StatusOK, `{"freshness":{"unavailable_regions":[]},"next_cursor":"s2","items":[
			{"api_id":13,"uid":5,"since":"2026-11-07T04:00:00Z"}
		]}`},
		reply{http.StatusServiceUnavailable, ""},
	)
	enqueue(t, ctx, db)
	if status, class := waitJob(t, ctx, db, 5); status != "failed" || class != "unavailable" {
		t.Fatalf("page 2 %s %s", status, class)
	}
	got = scopeRows(t, ctx, db)
	if _, ok := got[key(13, 5)]; !ok {
		t.Fatal("page 1 was rolled back")
	}
	_, complete, regions = scopeState(t, ctx, db)
	if complete == nil || !complete.Equal(*frozen) || len(regions) != 2 {
		t.Fatalf("error round moved state complete %v regions %v", complete, regions)
	}
	failedQueries := take()
	if len(failedQueries) != 2 || strings.Contains(failedQueries[0], "cursor=") {
		t.Fatalf("failed queries %v", failedQueries)
	}

	take()
	push(reply{http.StatusOK, `{"freshness":{"unavailable_regions":[]},"items":[]}`})
	enqueue(t, ctx, db)
	if status, class := waitJob(t, ctx, db, 6); status != "succeeded" || class != "" {
		t.Fatalf("restart %s %s", status, class)
	}
	restarted := take()
	if len(restarted) != 1 || strings.Contains(restarted[0], "cursor=") {
		t.Fatalf("restart query %v", restarted)
	}
	if _, _, regions = scopeState(t, ctx, db); len(regions) != 0 {
		t.Fatalf("regions after a complete round %v", regions)
	}
	before := scopeRows(t, ctx, db)

	srv.Close()
	enqueue(t, ctx, db)
	status, class := waitJob(t, ctx, db, 7)
	if status != "failed" || class != "unavailable" {
		t.Fatalf("down run %s %s", status, class)
	}
	if diff := scopeDiff(before, scopeRows(t, ctx, db)); diff != "" {
		t.Fatal(diff)
	}
}

func TestAccountFailureSkipsScopes(t *testing.T) {
	ctx, db := testDB(t)
	var scopes atomic.Int32
	client := skipScopes{scopes: &scopes}
	enqueue(t, ctx, db)
	startWorker(t, &worker.Worker{
		DB: db, Name: "w", Slots: 1, Poll: 20 * time.Millisecond, BudgetTime: time.Minute,
		Handlers: worker.Handlers{reconcile.MftikoSync.Name: MftikoSync(client)},
	})
	if status, class := waitJob(t, ctx, db, 1); status != "failed" || class != "unavailable" {
		t.Fatalf("%s %s", status, class)
	}
	if scopes.Load() != 0 {
		t.Fatalf("scope reads %d", scopes.Load())
	}
	var n int
	if err := db.QueryRow(ctx, `SELECT count(*) FROM mirror.scope`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Fatalf("scope rows %d", n)
	}
	if err := db.QueryRow(ctx, `SELECT count(*) FROM mirror.sync_state WHERE source = 'mftiko.scopes'`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Fatalf("scope state rows %d", n)
	}
}

type skipScopes struct{ scopes *atomic.Int32 }

func (s skipScopes) ListAccounts(context.Context, string) (mftiko.Page, error) {
	return mftiko.Page{}, &classify.ClassifiedError{Class: classify.Unavailable, Detail: "down"}
}

func (s skipScopes) ListScopes(context.Context, string) (mftiko.ScopePage, error) {
	s.scopes.Add(1)
	return mftiko.ScopePage{}, nil
}

func key(apiID, uid int64) [2]int64 { return [2]int64{apiID, uid} }

type scopeRow struct {
	Since, First, Last time.Time
	Lowered            *time.Time
}

func scopeRows(t *testing.T, ctx context.Context, db *pgxpool.Pool) map[[2]int64]scopeRow {
	t.Helper()
	rows, err := db.Query(ctx, `
		SELECT api_id, uid, since, first_seen_at, since_lowered_at, last_seen_at
		FROM mirror.scope ORDER BY api_id, uid`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	out := map[[2]int64]scopeRow{}
	for rows.Next() {
		var apiID, uid int64
		var row scopeRow
		if err := rows.Scan(&apiID, &uid, &row.Since, &row.First, &row.Lowered, &row.Last); err != nil {
			t.Fatal(err)
		}
		out[key(apiID, uid)] = row
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	return out
}

func scopeState(t *testing.T, ctx context.Context, db *pgxpool.Pool) (items int, complete *time.Time, regions []string) {
	t.Helper()
	if err := db.QueryRow(ctx, `
		SELECT items, last_complete_at, unavailable_regions
		FROM mirror.sync_state WHERE source = 'mftiko.scopes'`).Scan(&items, &complete, &regions); err != nil {
		t.Fatal(err)
	}
	return items, complete, regions
}

func scopeDiff(before, after map[[2]int64]scopeRow) string {
	if len(before) != len(after) {
		return "row count changed"
	}
	for id, row := range before {
		got, ok := after[id]
		if !ok || !got.Since.Equal(row.Since) || !got.First.Equal(row.First) || !got.Last.Equal(row.Last) || (got.Lowered == nil) != (row.Lowered == nil) {
			return "a scope row changed"
		}
		if got.Lowered != nil && !got.Lowered.Equal(*row.Lowered) {
			return "since_lowered_at changed"
		}
	}
	return ""
}
