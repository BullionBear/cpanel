package api

import (
	"context"
	"encoding/json"
	"io/fs"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/rs/zerolog"

	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/migrations"
	"github.com/BullionBear/cpanel/web"
)

func TestHealthz(t *testing.T) {
	rec := do(t, handler(t, nil, ""), http.MethodGet, "/healthz")
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d body %s", rec.Code, rec.Body)
	}
	if ct := rec.Header().Get("Content-Type"); !strings.Contains(ct, "application/json") {
		t.Fatalf("content-type %q", ct)
	}
	var raw map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &raw); err != nil {
		t.Fatal(err)
	}
	version, _ := raw["version"].(string)
	if _, ok := raw["commit"]; !ok || raw["ok"] != true || version == "" {
		t.Fatalf("%s", rec.Body)
	}
}

func TestReadyz(t *testing.T) {
	db := pgtest.Start(t)
	ctx := context.Background()
	if _, err := migrations.Up(ctx, db.DSN()); err != nil {
		t.Fatal(err)
	}
	pool := poolFor(t, db.DSN())

	t.Run("current", func(t *testing.T) {
		rec := do(t, handler(t, pool, db.DSN()), http.MethodGet, "/readyz")
		if rec.Code != http.StatusOK {
			t.Fatalf("status %d body %s", rec.Code, rec.Body)
		}
		var body struct {
			OK bool `json:"ok"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
			t.Fatal(err)
		}
		if !body.OK {
			t.Fatalf("%s", rec.Body)
		}
	})

	t.Run("schema behind", func(t *testing.T) {
		fresh := pgtest.Start(t)
		behind := poolFor(t, fresh.DSN())
		rec := do(t, handler(t, behind, fresh.DSN()), http.MethodGet, "/readyz")
		p := problemOf(t, rec, http.StatusServiceUnavailable, "unavailable")
		if p.Detail != "database schema is not current" || strings.Contains(rec.Body.String(), fresh.DSN()) {
			t.Fatalf("%s", rec.Body)
		}
		health := do(t, handler(t, behind, fresh.DSN()), http.MethodGet, "/healthz")
		if health.Code != http.StatusOK {
			t.Fatalf("healthz %d", health.Code)
		}
	})

	t.Run("database down", func(t *testing.T) {
		h := handler(t, pool, db.DSN())
		db.Stop()
		health := do(t, h, http.MethodGet, "/healthz")
		if health.Code != http.StatusOK {
			t.Fatalf("healthz %d", health.Code)
		}
		rec := do(t, h, http.MethodGet, "/readyz")
		p := problemOf(t, rec, http.StatusServiceUnavailable, "unavailable")
		if p.Detail != "database is not reachable" || strings.Contains(rec.Body.String(), "postgres://") {
			t.Fatalf("%s", rec.Body)
		}
	})
}

func TestMetrics(t *testing.T) {
	rec := do(t, handler(t, nil, ""), http.MethodGet, "/metrics")
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	if ct := rec.Header().Get("Content-Type"); !strings.Contains(ct, "text/plain") {
		t.Fatalf("content-type %q", ct)
	}
	if !strings.Contains(rec.Body.String(), "go_goroutines") {
		t.Fatalf("body %s", rec.Body)
	}
}

func TestSPA(t *testing.T) {
	h := handler(t, nil, "")
	index, err := fs.ReadFile(web.Build, "build/index.html")
	if err != nil {
		t.Fatal(err)
	}
	assetPath, js := embeddedJS(t)
	embeddedHas(t, "/auth/discord/login")
	embeddedHas(t, "Accounts")

	root := do(t, h, http.MethodGet, "/")
	if root.Code != http.StatusOK || root.Body.String() != string(index) {
		t.Fatalf("GET / = %d %q", root.Code, root.Body)
	}
	if root.Header().Get("Cache-Control") != "no-cache" || !strings.Contains(root.Header().Get("Content-Type"), "text/html") {
		t.Fatalf("headers %v", root.Header())
	}

	page := do(t, h, http.MethodGet, "/trades/1")
	if page.Code != http.StatusOK || page.Body.String() != string(index) || page.Header().Get("Cache-Control") != "no-cache" {
		t.Fatalf("GET /trades/1 = %d %q %q", page.Code, page.Header().Get("Cache-Control"), page.Body)
	}

	asset := do(t, h, http.MethodGet, assetPath)
	if asset.Code != http.StatusOK || asset.Body.String() != string(js) {
		t.Fatalf("asset %d %q", asset.Code, asset.Body)
	}
	if asset.Header().Get("Cache-Control") != "public, max-age=31536000, immutable" {
		t.Fatalf("cache %q", asset.Header().Get("Cache-Control"))
	}

	head := do(t, h, http.MethodHead, "/trades/1")
	if head.Code != http.StatusOK || head.Body.Len() != 0 || head.Header().Get("Cache-Control") != "no-cache" {
		t.Fatalf("HEAD %d %q %q", head.Code, head.Header().Get("Cache-Control"), head.Body)
	}

	post := do(t, h, http.MethodPost, "/trades/1")
	problemOf(t, post, http.StatusMethodNotAllowed, "method_not_allowed")
}

func TestMethodNotAllowed(t *testing.T) {
	rec := do(t, handler(t, nil, ""), http.MethodPost, "/healthz")
	problemOf(t, rec, http.StatusMethodNotAllowed, "method_not_allowed")
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

func embeddedHas(t *testing.T, needle string) {
	t.Helper()
	found := false
	err := fs.WalkDir(web.Build, "build", func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || found {
			return err
		}
		if !strings.HasSuffix(p, ".js") && !strings.HasSuffix(p, ".html") {
			return nil
		}
		b, err := fs.ReadFile(web.Build, p)
		if err != nil {
			return err
		}
		if strings.Contains(string(b), needle) {
			found = true
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if !found {
		t.Fatalf("embedded SPA does not contain %q", needle)
	}
}

func handler(t *testing.T, pool *pgxpool.Pool, dsn string) http.Handler {
	t.Helper()
	return Handler(Config{Log: zerolog.Nop(), Pool: pool, Database: dsn, Timeout: 2 * time.Second})
}

func poolFor(t *testing.T, dsn string) *pgxpool.Pool {
	t.Helper()
	pool, err := pgxpool.New(context.Background(), dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	return pool
}

func do(t *testing.T, h http.Handler, method, path string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

type problemJSON struct {
	Type   string `json:"type"`
	Title  string `json:"title"`
	Status int    `json:"status"`
	Detail string `json:"detail"`
	Code   string `json:"code"`
}

func problemOf(t *testing.T, rec *httptest.ResponseRecorder, status int, code string) problemJSON {
	t.Helper()
	if rec.Code != status {
		t.Fatalf("status %d, want %d, body %s", rec.Code, status, rec.Body)
	}
	if ct := rec.Header().Get("Content-Type"); !strings.Contains(ct, "application/problem+json") {
		t.Fatalf("content-type %q", ct)
	}
	var p problemJSON
	if err := json.Unmarshal(rec.Body.Bytes(), &p); err != nil {
		t.Fatal(err)
	}
	if p.Type != "about:blank" || p.Title == "" || p.Status != status || p.Code != code {
		t.Fatalf("%+v", p)
	}
	return p
}
