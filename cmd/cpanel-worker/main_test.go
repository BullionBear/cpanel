package main

import (
	"context"
	"io"
	"net/http"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/rs/zerolog"

	"github.com/BullionBear/cpanel/internal/cmdtest"
	"github.com/BullionBear/cpanel/internal/platform/logger"
	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/migrations"
)

func TestParseArgs(t *testing.T) {
	cases := []struct {
		name string
		env  map[string]string
		args []string
		want options
	}{
		{"defaults", nil, nil, options{listen: ":8081", logLevel: logger.DefaultLevel, shutdownTimeout: 10 * time.Second, reconcileInterval: 5 * time.Second, jobSlots: 4}},
		{"environment", map[string]string{
			"CPANEL_WORKER_LISTEN": ":9001", "CPANEL_LOG_LEVEL": "debug", "CPANEL_SHUTDOWN_TIMEOUT": "3s",
			"CPANEL_DATABASE_URL": "postgres://db/cpanel", "CPANEL_RECONCILE_INTERVAL": "2s", "CPANEL_JOB_SLOTS": "2",
			"CPANEL_EGRESS":     "172.237.19.123",
			"CPANEL_API_LISTEN": ":1", // the API's, not ours
		}, nil, options{listen: ":9001", database: "postgres://db/cpanel", logLevel: logger.Level(zerolog.DebugLevel), shutdownTimeout: 3 * time.Second, reconcileInterval: 2 * time.Second, jobSlots: 2, egress: "172.237.19.123"}},
		{"flags over environment", map[string]string{"CPANEL_WORKER_LISTEN": ":9001", "CPANEL_LOG_LEVEL": "debug", "CPANEL_DATABASE_URL": "postgres://env/cpanel", "CPANEL_RECONCILE_INTERVAL": "2s", "CPANEL_JOB_SLOTS": "2", "CPANEL_EGRESS": "env"},
			[]string{"-listen=:7001", "-log-level=warn", "-shutdown-timeout=1m", "-database=postgres://flag/cpanel", "-reconcile-interval=1s", "-job-slots=8", "-egress=flag"},
			options{listen: ":7001", database: "postgres://flag/cpanel", logLevel: logger.Level(zerolog.WarnLevel), shutdownTimeout: time.Minute, reconcileInterval: time.Second, jobSlots: 8, egress: "flag"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, _, err := parseArgs(c.args, func(k string) string { return c.env[k] })
			if err != nil {
				t.Fatal(err)
			}
			if got != c.want {
				t.Errorf("got %+v, want %+v", got, c.want)
			}
		})
	}
	if _, _, err := parseArgs(nil, func(k string) string { return map[string]string{"CPANEL_LOG_LEVEL": "loud"}[k] }); err == nil {
		t.Error("accepted CPANEL_LOG_LEVEL=loud")
	}
	if got, _, err := parseArgs([]string{"-sym-url=http://sym", "doctor"}, nil); err != nil || !got.doctor || got.symURL != "http://sym" {
		t.Errorf("doctor: %+v, %v", got, err)
	}
	if _, _, err := parseArgs([]string{"serve"}, nil); err == nil {
		t.Error("accepted an unknown command")
	}
	for _, bad := range []string{"0s", "-1s"} {
		_, _, err := parseArgs(nil, func(k string) string { return map[string]string{"CPANEL_RECONCILE_INTERVAL": bad}[k] })
		if err == nil || !strings.Contains(err.Error(), "$CPANEL_RECONCILE_INTERVAL") {
			t.Errorf("CPANEL_RECONCILE_INTERVAL=%s: %v", bad, err)
		}
	}
	for _, bad := range []string{"0", "-1", "four"} {
		_, _, err := parseArgs(nil, func(k string) string { return map[string]string{"CPANEL_JOB_SLOTS": bad}[k] })
		if err == nil || !strings.Contains(err.Error(), "$CPANEL_JOB_SLOTS") {
			t.Errorf("CPANEL_JOB_SLOTS=%s: %v", bad, err)
		}
	}
}

func TestBinary(t *testing.T) {
	bin := cmdtest.Build(t)
	srv := pgtest.Start(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if _, err := migrations.Up(ctx, srv.DSN()); err != nil {
		t.Fatal(err)
	}

	t.Run("healthz, reconcile, jobs, metrics and graceful shutdown", func(t *testing.T) {
		p := cmdtest.Start(t, bin, []string{"CPANEL_WORKER_LISTEN=127.0.0.1:0", "CPANEL_DATABASE_URL=" + srv.DSN(), "CPANEL_EGRESS=edge"},
			"-shutdown-timeout=5s", "-reconcile-interval=50ms")
		start := p.Log(t, "starting")
		cfg, _ := start["config"].(map[string]any)
		if start["component"] != name || cfg["listen"] != "127.0.0.1:0" || cfg["database"] != "***" || cfg["log-level"] != "info" ||
			cfg["shutdown-timeout"] != "5s" || cfg["reconcile-interval"] != "50ms" || cfg["job-slots"] != "4" || cfg["egress"] != "edge" {
			t.Errorf("startup line %v", start)
		}
		addr, _ := p.Log(t, "listening")["addr"].(string)

		resp, err := http.Get("http://" + addr + "/healthz")
		if err != nil {
			t.Fatal(err)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK || strings.TrimSpace(string(body)) != `{"ok":true}` {
			t.Errorf("GET /healthz = %d %q", resp.StatusCode, body)
		}

		// The first round registers maint.partitions and dispatches its job; the job runner runs it, making today's
		// partition and the next 7 days'.
		conn, err := pgx.Connect(ctx, srv.DSN())
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close(context.Background())
		var runs, items, jobs, parts int
		for deadline := time.Now().Add(10 * time.Second); ; time.Sleep(50 * time.Millisecond) {
			err := conn.QueryRow(ctx, `
				SELECT (SELECT count(*) FROM ingest.job_run WHERE periodic_name = 'maint.partitions' AND status = 'succeeded'),
				       (SELECT coalesce(sum(items), 0) FROM ingest.job_run WHERE periodic_name = 'maint.partitions'),
				       (SELECT count(*) FROM ingest.job),
				       (SELECT count(*) FROM pg_inherits WHERE inhparent = 'ingest.job_run'::regclass)`).Scan(&runs, &items, &jobs, &parts)
			if err != nil {
				t.Fatal(err)
			}
			if runs == 1 || time.Now().After(deadline) {
				break
			}
		}
		if runs != 1 || items != 8 || jobs != 0 || parts != 1+8 {
			t.Fatalf("after 10s: %d maint.partitions runs with %d items, %d jobs left, %d job_run partitions; want 1 with 8, 0 and 9",
				runs, items, jobs, parts)
		}
		resp, err = http.Get("http://" + addr + "/metrics")
		if err != nil {
			t.Fatal(err)
		}
		body, _ = io.ReadAll(resp.Body)
		resp.Body.Close()
		for _, want := range []string{
			`cpanel_reconcile_inserted_total{rule="maint.partitions"} 1`,
			`cpanel_reconcile_inserted_total{rule="mftiko.sync"} 1`,
			`cpanel_reconcile_inserted_total{rule="sym.sync"} 1`,
			`cpanel_reconcile_inserted_total{rule="periodics"} 3`,
			`cpanel_reconcile_inserted_total{rule="streams"} 0`,
			`cpanel_reconcile_rule_duration_seconds_count{rule="streams"}`,
			`cpanel_reconcile_rounds_total{result="error"} 0`,
			`cpanel_reconcile_rounds_total{result="ok"}`,
			`go_goroutines`,
		} {
			if !strings.Contains(string(body), want) {
				t.Errorf("GET /metrics = %d, no %q", resp.StatusCode, want)
			}
		}

		p.Signal(t, syscall.SIGINT)
		p.Log(t, "stopped")
		if code := p.Exit(t); code != 0 {
			t.Errorf("exit %d after SIGINT, want 0", code)
		}
	})

	t.Run("doctor without settings fails every check", func(t *testing.T) {
		code, _ := cmdtest.Run(t, bin, nil, "doctor")
		if code != 1 {
			t.Errorf("doctor exit %d, want 1", code)
		}
	})

	t.Run("bad setting exits 2", func(t *testing.T) {
		code, stderr := cmdtest.Run(t, bin, []string{"CPANEL_LOG_LEVEL=loud"})
		if code != 2 || !strings.Contains(stderr, "$CPANEL_LOG_LEVEL") {
			t.Errorf("exit %d, stderr %q", code, stderr)
		}
	})

	t.Run("behind database is refused", func(t *testing.T) {
		fresh := pgtest.Start(t)
		code, stderr := cmdtest.Run(t, bin, []string{"CPANEL_DATABASE_URL=" + fresh.DSN()})
		if code != 1 || !strings.Contains(stderr, "behind") || !strings.Contains(stderr, "0001_schema") {
			t.Errorf("exit %d, stderr %q", code, stderr)
		}
	})
}
