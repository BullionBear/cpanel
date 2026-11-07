package main

import (
	"context"
	"io"
	"net/http"
	"strings"
	"syscall"
	"testing"
	"time"

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
		{"defaults", nil, nil, options{listen: ":8081", logLevel: logger.DefaultLevel, shutdownTimeout: 10 * time.Second}},
		{"environment", map[string]string{
			"CPANEL_WORKER_LISTEN": ":9001", "CPANEL_LOG_LEVEL": "debug", "CPANEL_SHUTDOWN_TIMEOUT": "3s",
			"CPANEL_DATABASE_URL": "postgres://db/cpanel",
			"CPANEL_API_LISTEN":   ":1", // the API's, not ours
		}, nil, options{listen: ":9001", database: "postgres://db/cpanel", logLevel: logger.Level(zerolog.DebugLevel), shutdownTimeout: 3 * time.Second}},
		{"flags over environment", map[string]string{"CPANEL_WORKER_LISTEN": ":9001", "CPANEL_LOG_LEVEL": "debug", "CPANEL_DATABASE_URL": "postgres://env/cpanel"},
			[]string{"-listen=:7001", "-log-level=warn", "-shutdown-timeout=1m", "-database=postgres://flag/cpanel"},
			options{listen: ":7001", database: "postgres://flag/cpanel", logLevel: logger.Level(zerolog.WarnLevel), shutdownTimeout: time.Minute}},
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
}

func TestBinary(t *testing.T) {
	bin := cmdtest.Build(t)
	srv := pgtest.Start(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if _, err := migrations.Up(ctx, srv.DSN()); err != nil {
		t.Fatal(err)
	}

	t.Run("healthz and graceful shutdown", func(t *testing.T) {
		p := cmdtest.Start(t, bin, []string{"CPANEL_WORKER_LISTEN=127.0.0.1:0", "CPANEL_DATABASE_URL=" + srv.DSN()}, "-shutdown-timeout=5s")
		start := p.Log(t, "starting")
		cfg, _ := start["config"].(map[string]any)
		if start["component"] != name || cfg["listen"] != "127.0.0.1:0" || cfg["database"] != "***" || cfg["log-level"] != "info" || cfg["shutdown-timeout"] != "5s" {
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
