package main

import (
	"io"
	"net/http"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/rs/zerolog"

	"github.com/BullionBear/cpanel/internal/cmdtest"
	"github.com/BullionBear/cpanel/internal/platform/logger"
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
			"CPANEL_API_LISTEN": ":1", // the API's, not ours
		}, nil, options{listen: ":9001", logLevel: logger.Level(zerolog.DebugLevel), shutdownTimeout: 3 * time.Second}},
		{"flags over environment", map[string]string{"CPANEL_WORKER_LISTEN": ":9001", "CPANEL_LOG_LEVEL": "debug"},
			[]string{"-listen=:7001", "-log-level=warn", "-shutdown-timeout=1m"},
			options{listen: ":7001", logLevel: logger.Level(zerolog.WarnLevel), shutdownTimeout: time.Minute}},
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
}

func TestBinary(t *testing.T) {
	bin := cmdtest.Build(t)

	t.Run("healthz and graceful shutdown", func(t *testing.T) {
		p := cmdtest.Start(t, bin, []string{"CPANEL_WORKER_LISTEN=127.0.0.1:0"}, "-shutdown-timeout=5s")
		start := p.Log(t, "starting")
		cfg, _ := start["config"].(map[string]any)
		if start["component"] != name || cfg["listen"] != "127.0.0.1:0" || cfg["log-level"] != "info" || cfg["shutdown-timeout"] != "5s" {
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

	t.Run("bad setting exits 2", func(t *testing.T) {
		code, stderr := cmdtest.Run(t, bin, []string{"CPANEL_LOG_LEVEL=loud"})
		if code != 2 || !strings.Contains(stderr, "$CPANEL_LOG_LEVEL") {
			t.Errorf("exit %d, stderr %q", code, stderr)
		}
	})
}
