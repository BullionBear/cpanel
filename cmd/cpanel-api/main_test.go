package main

import (
	"context"
	"encoding/json"
	"io"
	"io/fs"
	"net/http"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/rs/zerolog"

	"github.com/BullionBear/cpanel/internal/auth"
	"github.com/BullionBear/cpanel/internal/cmdtest"
	"github.com/BullionBear/cpanel/internal/platform/logger"
	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/migrations"
	"github.com/BullionBear/cpanel/web"
)

func TestParseArgs(t *testing.T) {
	cases := []struct {
		name string
		env  map[string]string
		args []string
		want options
	}{
		{"defaults", nil, nil, options{listen: ":8080", logLevel: logger.DefaultLevel, shutdownTimeout: 10 * time.Second, requestTimeout: 30 * time.Second, sessionTTL: auth.DefaultSessionTTL, roleRecheck: auth.DefaultRoleRecheck}},
		{"environment", map[string]string{
			"CPANEL_API_LISTEN": ":9000", "CPANEL_LOG_LEVEL": "debug", "CPANEL_SHUTDOWN_TIMEOUT": "3s", "CPANEL_REQUEST_TIMEOUT": "2s",
			"CPANEL_DATABASE_URL": "postgres://db/cpanel", "CPANEL_PUBLIC_ORIGIN": "https://cpanel.example",
			"CPANEL_DISCORD_CLIENT_ID": "cid", "CPANEL_DISCORD_CLIENT_SECRET": "sec", "CPANEL_SESSION_TTL": "2h",
			"CPANEL_WORKER_LISTEN": ":1", // the worker's, not ours
		}, nil, options{listen: ":9000", database: "postgres://db/cpanel", logLevel: logger.Level(zerolog.DebugLevel), shutdownTimeout: 3 * time.Second, requestTimeout: 2 * time.Second, publicOrigin: "https://cpanel.example", discordClientID: "cid", discordClientSecret: "sec", sessionTTL: 2 * time.Hour, roleRecheck: auth.DefaultRoleRecheck}},
		{"flags over environment", map[string]string{"CPANEL_API_LISTEN": ":9000", "CPANEL_LOG_LEVEL": "debug", "CPANEL_DATABASE_URL": "postgres://env/cpanel", "CPANEL_REQUEST_TIMEOUT": "2s", "CPANEL_DISCORD_CLIENT_SECRET": "env-secret"},
			[]string{"-listen=:7000", "-log-level=warn", "-shutdown-timeout=1m", "-request-timeout=1s", "-database=postgres://flag/cpanel", "-discord-client-secret=flag-secret", "-session-ttl=1h"},
			options{listen: ":7000", database: "postgres://flag/cpanel", logLevel: logger.Level(zerolog.WarnLevel), shutdownTimeout: time.Minute, requestTimeout: time.Second, discordClientSecret: "flag-secret", sessionTTL: time.Hour, roleRecheck: auth.DefaultRoleRecheck}},
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
	srv := pgtest.Start(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if _, err := migrations.Up(ctx, srv.DSN()); err != nil {
		t.Fatal(err)
	}

	t.Run("healthz and graceful shutdown", func(t *testing.T) {
		p := cmdtest.Start(t, bin, []string{"CPANEL_API_LISTEN=127.0.0.1:0", "CPANEL_DATABASE_URL=" + srv.DSN(), "CPANEL_DISCORD_CLIENT_SECRET=supersecret", "CPANEL_DISCORD_BOT_TOKEN=botsecret"}, "-shutdown-timeout=5s")
		start := p.Log(t, "starting")
		cfg, _ := start["config"].(map[string]any)
		if start["component"] != name || cfg["listen"] != "127.0.0.1:0" || cfg["database"] != "***" || cfg["discord-client-secret"] != "***" || cfg["discord-bot-token"] != "***" || cfg["log-level"] != "info" || cfg["shutdown-timeout"] != "5s" || cfg["request-timeout"] != "30s" {
			t.Errorf("startup line %v", start)
		}
		addr, _ := p.Log(t, "listening")["addr"].(string)

		health := getJSON[struct {
			OK      bool   `json:"ok"`
			Version string `json:"version"`
			Commit  string `json:"commit"`
		}](t, addr, "/healthz")
		if health.code != http.StatusOK || !health.body.OK || health.body.Version == "" || health.body.Commit == "" {
			t.Errorf("GET /healthz = %d %+v", health.code, health.body)
		}
		ready := getJSON[struct {
			OK bool `json:"ok"`
		}](t, addr, "/readyz")
		if ready.code != http.StatusOK || !ready.body.OK {
			t.Errorf("GET /readyz = %d %+v", ready.code, ready.body)
		}
		index, err := fs.ReadFile(web.Build, "build/index.html")
		if err != nil {
			t.Fatal(err)
		}
		root := getRaw(t, addr, "/")
		page := getRaw(t, addr, "/trades/1")
		if root.code != http.StatusOK || page.code != http.StatusOK || string(root.body) != string(index) || string(page.body) != string(index) {
			t.Errorf("SPA / = %d %q, /trades/1 = %d %q", root.code, root.body, page.code, page.body)
		}
		if root.header.Get("Cache-Control") != "no-cache" || page.header.Get("Cache-Control") != "no-cache" {
			t.Errorf("SPA cache / %q /trades/1 %q", root.header.Get("Cache-Control"), page.header.Get("Cache-Control"))
		}
		assetPath, assetBody := embeddedJS(t)
		asset := getRaw(t, addr, assetPath)
		if asset.code != http.StatusOK || asset.header.Get("Cache-Control") != "public, max-age=31536000, immutable" || string(asset.body) != string(assetBody) {
			t.Errorf("asset %d %q len %d", asset.code, asset.header.Get("Cache-Control"), len(asset.body))
		}

		p.Signal(t, syscall.SIGTERM)
		p.Log(t, "stopped")
		if code := p.Exit(t); code != 0 {
			t.Errorf("exit %d after SIGTERM, want 0", code)
		}
	})

	t.Run("bad setting exits 2", func(t *testing.T) {
		code, stderr := cmdtest.Run(t, bin, []string{"CPANEL_SHUTDOWN_TIMEOUT=soon"})
		if code != 2 || !strings.Contains(stderr, "$CPANEL_SHUTDOWN_TIMEOUT") {
			t.Errorf("exit %d, stderr %q", code, stderr)
		}
	})

	t.Run("help exits 0", func(t *testing.T) {
		code, stderr := cmdtest.Run(t, bin, nil, "-h")
		if code != 0 || !strings.Contains(stderr, "$CPANEL_API_LISTEN") || !strings.Contains(stderr, "$CPANEL_DATABASE_URL") || !strings.Contains(stderr, "$CPANEL_REQUEST_TIMEOUT") || !strings.Contains(stderr, "$CPANEL_DISCORD_CLIENT_SECRET") {
			t.Errorf("exit %d, stderr %q", code, stderr)
		}
	})

	t.Run("empty database exits 2", func(t *testing.T) {
		code, stderr := cmdtest.Run(t, bin, nil)
		if code != 2 || !strings.Contains(stderr, "CPANEL_DATABASE_URL") {
			t.Errorf("exit %d, stderr %q", code, stderr)
		}
	})

	t.Run("database down", func(t *testing.T) {
		p := cmdtest.Start(t, bin, []string{"CPANEL_API_LISTEN=127.0.0.1:0", "CPANEL_DATABASE_URL=" + srv.DSN()})
		addr, _ := p.Log(t, "listening")["addr"].(string)
		srv.Stop()

		health := getRaw(t, addr, "/healthz")
		if health.code != http.StatusOK {
			t.Errorf("GET /healthz = %d %s", health.code, health.body)
		}
		ready := getRaw(t, addr, "/readyz")
		if ready.code != http.StatusServiceUnavailable || !strings.Contains(ready.header.Get("Content-Type"), "application/problem+json") {
			t.Fatalf("GET /readyz = %d %q %s", ready.code, ready.header.Get("Content-Type"), ready.body)
		}
		var problem struct {
			Type   string `json:"type"`
			Status int    `json:"status"`
			Code   string `json:"code"`
			Detail string `json:"detail"`
		}
		if err := json.Unmarshal(ready.body, &problem); err != nil {
			t.Fatal(err)
		}
		if problem.Type != "about:blank" || problem.Status != http.StatusServiceUnavailable || problem.Code != "unavailable" || problem.Detail != "database is not reachable" || strings.Contains(string(ready.body), "postgres://") {
			t.Errorf("problem %+v body %s", problem, ready.body)
		}
		p.Signal(t, syscall.SIGTERM)
		if code := p.Exit(t); code != 0 {
			t.Errorf("exit %d after SIGTERM, want 0", code)
		}
	})

	t.Run("behind database is refused", func(t *testing.T) {
		fresh := pgtest.Start(t)
		code, stderr := cmdtest.Run(t, bin, []string{"CPANEL_DATABASE_URL=" + fresh.DSN()})
		if code != 1 || !strings.Contains(stderr, "behind") || !strings.Contains(stderr, "0001_schema") || !strings.Contains(stderr, "cpanel-migrate up") {
			t.Errorf("exit %d, stderr %q", code, stderr)
		}
	})
}

type rawResponse struct {
	code   int
	header http.Header
	body   []byte
}

func embeddedJS(t *testing.T) (string, []byte) {
	t.Helper()
	var urlPath string
	var body []byte
	err := fs.WalkDir(web.Build, "build", func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || urlPath != "" {
			return err
		}
		if strings.Contains(p, "/_app/immutable/") && strings.HasSuffix(p, ".js") {
			body, err = fs.ReadFile(web.Build, p)
			if err != nil {
				return err
			}
			urlPath = "/" + strings.TrimPrefix(p, "build/")
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if urlPath == "" || len(body) == 0 {
		t.Fatal("embedded SPA has no immutable javascript")
	}
	return urlPath, body
}

func getRaw(t *testing.T, addr, path string) rawResponse {
	t.Helper()
	resp, err := http.Get("http://" + addr + path)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return rawResponse{code: resp.StatusCode, header: resp.Header, body: body}
}

type jsonResponse[T any] struct {
	code int
	body T
}

func getJSON[T any](t *testing.T, addr, path string) jsonResponse[T] {
	t.Helper()
	raw := getRaw(t, addr, path)
	var body T
	if err := json.Unmarshal(raw.body, &body); err != nil {
		t.Fatalf("%s: %v body %s", path, err, raw.body)
	}
	return jsonResponse[T]{code: raw.code, body: body}
}
