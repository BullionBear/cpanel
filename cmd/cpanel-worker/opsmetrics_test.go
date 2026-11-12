package main

import (
	"context"
	"strconv"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
	dto "github.com/prometheus/client_model/go"
	"github.com/rs/zerolog"

	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/migrations"
)

func TestOpsMetrics(t *testing.T) {
	srv := pgtest.Start(t)
	ctx := context.Background()
	if _, err := migrations.Up(ctx, srv.DSN()); err != nil {
		t.Fatal(err)
	}
	pool, err := pgxpool.New(ctx, srv.DSN())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)

	var failing, paused int64
	if err := pool.QueryRow(ctx, `
		INSERT INTO ingest.stream (kind, lease_key, key, source, period, last_ok_at, consecutive_failures, last_error_class)
		VALUES ('trade', 'account:1', 'a', 'manual', interval '1 hour', now() - interval '30 minutes', 3, 'unavailable')
		RETURNING id`).Scan(&failing); err != nil {
		t.Fatal(err)
	}
	if err := pool.QueryRow(ctx, `
		INSERT INTO ingest.stream (kind, lease_key, key, source, period, paused_reason, paused_at, consecutive_failures)
		VALUES ('trade', 'account:1', 'b', 'manual', interval '1 hour', 'bad_request', now(), 9)
		RETURNING id`).Scan(&paused); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO ingest.pause (scope, until, reason) VALUES ('egress:test', now() + interval '1 hour', 'ip_banned')`); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, status, enqueued_at)
		VALUES ('trade', 'stream:old', 'account:1', 'pending', now() - interval '20 minutes')`); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO ingest.job_run (
			id, kind, dedup_key, lease_key, priority, attempt, params, enqueued_at, finished_at, status, error_class, error)
		VALUES (1, 'trade', 'stream:old', 'account:1', 100, 1, '{}', now() - interval '2 minutes', now() - interval '1 minute',
		        'failed', 'unavailable', 'down')`); err != nil {
		t.Fatal(err)
	}

	reg := prometheus.NewRegistry()
	reg.MustRegister(newOpsCollector(pool, zerolog.Nop()))
	fams, err := reg.Gather()
	if err != nil {
		t.Fatal(err)
	}
	id := strconv.FormatInt(failing, 10)
	if got := gauge(t, fams, "cpanel_stream_consecutive_failures", map[string]string{"kind": "trade", "stream": id}); got != 3 {
		t.Fatalf("consecutive failures %v", got)
	}
	if got := gauge(t, fams, "cpanel_stream_lag_seconds", map[string]string{"kind": "trade", "stream": id}); got < 900 {
		t.Fatalf("lag %v", got)
	}
	if _, ok := findGauge(fams, "cpanel_stream_lag_seconds", map[string]string{"stream": strconv.FormatInt(paused, 10)}); ok {
		t.Fatal("paused stream is in the lag series")
	}
	if got := gauge(t, fams, "cpanel_streams_paused", nil); got != 1 {
		t.Fatalf("paused %v", got)
	}
	if got := gauge(t, fams, "cpanel_egress_pauses", nil); got != 1 {
		t.Fatalf("egress %v", got)
	}
	if got := gauge(t, fams, "cpanel_jobs_unfinished", map[string]string{"status": "pending"}); got != 1 {
		t.Fatalf("pending %v", got)
	}
	if got := gauge(t, fams, "cpanel_jobs_unfinished", map[string]string{"status": "running"}); got != 0 {
		t.Fatalf("running %v", got)
	}
	if got := gauge(t, fams, "cpanel_job_oldest_age_seconds", nil); got < 600 {
		t.Fatalf("oldest %v", got)
	}
	if got := gauge(t, fams, "cpanel_job_runs", map[string]string{"status": "failed", "error_class": "unavailable"}); got != 1 {
		t.Fatalf("runs %v", got)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO mirror.sync_state (source, last_attempt_at, last_ok_at)
		VALUES ('mftiko.accounts', now() - interval '20 minutes', now() - interval '12 minutes')`); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO mirror.account (api_id, name, kind, exchange, exch_account_uid, account_revision, first_seen_at, last_seen_at, identity_conflict)
		VALUES (1, 'a', 'BinanceUM', 'binance', 'u', 1, now(), now(), '{"exchange":"bybit"}')`); err != nil {
		t.Fatal(err)
	}
	fams, err = reg.Gather()
	if err != nil {
		t.Fatal(err)
	}
	if got := gauge(t, fams, "cpanel_mirror_ok_age_seconds", map[string]string{"source": "mftiko.accounts"}); got < 600 {
		t.Fatalf("mirror age %v", got)
	}
	if got := gauge(t, fams, "cpanel_mirror_rows", map[string]string{"table": "account"}); got != 1 {
		t.Fatalf("mirror rows %v", got)
	}
	if got := gauge(t, fams, "cpanel_mirror_account_identity_conflicts", nil); got != 1 {
		t.Fatalf("conflicts %v", got)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO mirror.symbol (
			uid, venue, category, exch_ticker, universal_ticker, base, quote, status, active, first_seen_at, last_seen_at)
		VALUES (1, 'BinanceUM', 'Perp', 'BTCUSDT', 'BinanceUM_Perp_BTCUSDT', 'BTC', 'USDT', 'TRADABLE', true, now(), now())`); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO mirror.sync_state (source, revision, remote_revision)
		VALUES ('sym:BinanceUM:Perp', 5, 6)`); err != nil {
		t.Fatal(err)
	}
	fams, err = reg.Gather()
	if err != nil {
		t.Fatal(err)
	}
	if got := gauge(t, fams, "cpanel_mirror_rows", map[string]string{"table": "symbol"}); got != 1 {
		t.Fatalf("symbol rows %v", got)
	}
	if got := gauge(t, fams, "cpanel_mirror_sym_tables_behind", nil); got != 1 {
		t.Fatalf("tables behind %v", got)
	}
	if got := gauge(t, fams, "cpanel_mirror_rows", map[string]string{"table": "scope"}); got != 0 {
		t.Fatalf("scope rows %v", got)
	}
	if got := gauge(t, fams, "cpanel_mirror_scope_unavailable_regions", nil); got != 0 {
		t.Fatalf("partial %v", got)
	}
	if got := gauge(t, fams, "cpanel_mirror_scope_uids_unresolved", nil); got != 0 {
		t.Fatalf("unresolved %v", got)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO mirror.scope (api_id, uid, since, first_seen_at, last_seen_at)
		VALUES (9, 99, '2026-11-07T01:02:03.123456Z', now(), now())`); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO mirror.sync_state (source, unavailable_regions)
		VALUES ('mftiko.scopes', ARRAY['eu','ap'])`); err != nil {
		t.Fatal(err)
	}
	fams, err = reg.Gather()
	if err != nil {
		t.Fatal(err)
	}
	if got := gauge(t, fams, "cpanel_mirror_rows", map[string]string{"table": "scope"}); got != 1 {
		t.Fatalf("scope rows %v", got)
	}
	if got := gauge(t, fams, "cpanel_mirror_scope_unavailable_regions", nil); got != 2 {
		t.Fatalf("partial %v", got)
	}
	if got := gauge(t, fams, "cpanel_mirror_scope_uids_unresolved", nil); got != 1 {
		t.Fatalf("unresolved %v", got)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO mirror.symbol (
			uid, venue, category, exch_ticker, universal_ticker, base, quote, status, active, first_seen_at, last_seen_at)
		VALUES (99, 'BinanceUM', 'Perp', 'BTCUSDT', 'BinanceUM_Perp_BTCUSDT', 'BTC', 'USDT', 'TRADING', true, now(), now())`); err != nil {
		t.Fatal(err)
	}
	fams, err = reg.Gather()
	if err != nil {
		t.Fatal(err)
	}
	if got := gauge(t, fams, "cpanel_mirror_scope_uids_unresolved", nil); got != 0 {
		t.Fatalf("unresolved after fill %v", got)
	}
}

func TestUnmatchedGauges(t *testing.T) {
	srv := pgtest.Start(t)
	ctx := context.Background()
	if _, err := migrations.Up(ctx, srv.DSN()); err != nil {
		t.Fatal(err)
	}
	pool, err := pgxpool.New(ctx, srv.DSN())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	seen := time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC)
	if _, err := pool.Exec(ctx, `
		INSERT INTO mirror.account (api_id, name, kind, exchange, exch_account_uid, account_revision, first_seen_at, last_seen_at)
		VALUES (1, 'spot', 'Binance', 'binance', 'uid', 1, $1, $1),
		       (2, 'um', 'BinanceUM', 'binance', 'uid', 1, $1, $1),
		       (3, 'pm', 'BinancePM', 'binance', 'uid-pm', 1, $1, $1)`, seen); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO mirror.scope (api_id, uid, since, first_seen_at, last_seen_at)
		VALUES (3, 9, $1, $1, $1)`, seen); err != nil {
		t.Fatal(err)
	}
	reg := prometheus.NewRegistry()
	reg.MustRegister(newOpsCollector(pool, zerolog.Nop()))
	fams, err := reg.Gather()
	if err != nil {
		t.Fatal(err)
	}
	if got := gauge(t, fams, "cpanel_mftiko_accounts_unmatched", map[string]string{"traded": "false", "reason": "no_exchange_account"}); got != 1 {
		t.Fatalf("unmatched %v", got)
	}
	if got := gauge(t, fams, "cpanel_mftiko_accounts_unmatched", map[string]string{"traded": "true", "reason": "no_exchange_account"}); got != 0 {
		t.Fatalf("traded unmatched %v", got)
	}
	if got := gauge(t, fams, "cpanel_mftiko_kinds_out_of_scope", map[string]string{"kind": "BinancePM", "traded": "true"}); got != 1 {
		t.Fatalf("pm %v", got)
	}
}

func gauge(t *testing.T, fams []*dto.MetricFamily, name string, labels map[string]string) float64 {
	t.Helper()
	got, ok := findGauge(fams, name, labels)
	if !ok {
		t.Fatalf("missing %s %v", name, labels)
	}
	return got
}

func findGauge(fams []*dto.MetricFamily, name string, labels map[string]string) (float64, bool) {
	for _, fam := range fams {
		if fam.GetName() != name {
			continue
		}
		for _, metric := range fam.GetMetric() {
			if labelsMatch(metric, labels) {
				return metric.GetGauge().GetValue(), true
			}
		}
	}
	return 0, false
}

func labelsMatch(metric *dto.Metric, labels map[string]string) bool {
	if labels == nil {
		return len(metric.GetLabel()) == 0
	}
	if len(metric.GetLabel()) != len(labels) {
		return false
	}
	for _, label := range metric.GetLabel() {
		if labels[label.GetName()] != label.GetValue() {
			return false
		}
	}
	return true
}
