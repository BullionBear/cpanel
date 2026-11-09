package mftiko

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/BullionBear/cpanel/internal/classify"
)

func TestListAccountsPagesDeletedAndKinds(t *testing.T) {
	var seen []string
	deleted := time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen = append(seen, r.Method+" "+r.URL.RequestURI())
		if r.Header.Get("Authorization") != "Bearer tok" {
			http.Error(w, "no", http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Query().Get("cursor") {
		case "":
			if r.URL.Query().Get("include_deleted") != "true" || r.URL.Query().Get("limit") != "1000" {
				t.Errorf("query %s", r.URL.RawQuery)
			}
			io.WriteString(w, `{
				"freshness":{"source":"primary"},
				"next_cursor":"p2",
				"items":[
					{"api_id":1,"name":"a","kind":"BinanceUM","exchange":"binance","exch_account_uid":"u1","revision":3,"referenced_by":[],"settings":{}},
					{"api_id":2,"name":"gone","kind":"BinancePM","exchange":"binance","exch_account_uid":"u2","revision":4,"deleted_at":"2026-11-01T00:00:00Z","referenced_by":[],"settings":{}}
				]}`)
		case "p2":
			io.WriteString(w, `{
				"freshness":{"source":"primary"},
				"items":[
					{"api_id":3,"name":"eo","kind":"BinanceEO","exchange":"binance","exch_account_uid":"u3","revision":1,"referenced_by":[],"settings":{}},
					{"api_id":4,"name":"odd","kind":"NotAKind","revision":1,"referenced_by":[],"settings":{}}
				]}`)
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(srv.Close)

	c, err := New(srv.URL+"/api/v1", "tok", srv.Client())
	if err != nil {
		t.Fatal(err)
	}
	first, err := c.ListAccounts(context.Background(), "")
	if err != nil {
		t.Fatal(err)
	}
	if first.Next != "p2" || len(first.Accounts) != 2 {
		t.Fatalf("%+v", first)
	}
	if first.Accounts[1].Kind != "BinancePM" || first.Accounts[1].DeletedAt == nil || !first.Accounts[1].DeletedAt.Equal(deleted) {
		t.Fatalf("deleted %+v", first.Accounts[1])
	}
	second, err := c.ListAccounts(context.Background(), first.Next)
	if err != nil {
		t.Fatal(err)
	}
	if second.Next != "" || len(second.Accounts) != 2 {
		t.Fatalf("%+v", second)
	}
	if second.Accounts[0].Kind != "BinanceEO" || second.Accounts[1].Kind != "NotAKind" || second.Accounts[1].Exchange != "" || second.Accounts[1].ExchAccountUID != "" {
		t.Fatalf("%+v", second.Accounts)
	}
	for _, call := range seen {
		if !strings.HasPrefix(call, "GET ") {
			t.Errorf("sent %s", call)
		}
	}
}

func TestListScopesTruncatesAndSkipsAPIID(t *testing.T) {
	var seen string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen = r.URL.RequestURI()
		if r.URL.Query().Get("api_id") != "" || r.URL.Query().Get("limit") != "1000" {
			t.Errorf("query %s", r.URL.RawQuery)
		}
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{
			"freshness":{"unavailable_regions":["eu"]},
			"items":[{"api_id":9,"uid":1,"since":"2026-11-07T01:02:03.123456789Z"}]
		}`)
	}))
	t.Cleanup(srv.Close)
	c, err := New(srv.URL+"/api/v1", "tok", srv.Client())
	if err != nil {
		t.Fatal(err)
	}
	page, err := c.ListScopes(context.Background(), "")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(seen, "/scopes") || strings.Contains(seen, "api_id") {
		t.Fatalf("uri %s", seen)
	}
	want := time.Date(2026, 11, 7, 1, 2, 3, 123456000, time.UTC)
	if page.Next != "" || len(page.Unavailable) != 1 || page.Unavailable[0] != "eu" || len(page.Scopes) != 1 {
		t.Fatalf("%+v", page)
	}
	if page.Scopes[0].APIID != 9 || page.Scopes[0].UID != 1 || !page.Scopes[0].Since.Equal(want) {
		t.Fatalf("%+v", page.Scopes[0])
	}

	bad := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"freshness":{"unavailable_regions":[]},"items":[{"api_id":1,"uid":0,"since":"2026-11-07T00:00:00Z"}]}`)
	}))
	t.Cleanup(bad.Close)
	c, err = New(bad.URL, "tok", bad.Client())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.ListScopes(context.Background(), ""); !classify.Is(err, classify.Internal) {
		t.Fatalf("uid 0: %v", err)
	}
}

func TestWritesNeverLeave(t *testing.T) {
	var seen []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen = append(seen, r.Method)
		w.WriteHeader(http.StatusNoContent)
	}))
	t.Cleanup(srv.Close)
	c, err := New(srv.URL, "tok", srv.Client())
	if err != nil {
		t.Fatal(err)
	}
	for _, method := range []string{http.MethodPost, http.MethodPut, http.MethodPatch, http.MethodDelete} {
		req, err := http.NewRequest(method, srv.URL+"/accounts", strings.NewReader(`{}`))
		if err != nil {
			t.Fatal(err)
		}
		if _, err := c.hc.Do(req); err == nil || !strings.Contains(err.Error(), "not allowed") {
			t.Errorf("%s: %v", method, err)
		}
	}
	if len(seen) != 0 {
		t.Fatalf("server saw %v", seen)
	}
}

func TestClassify(t *testing.T) {
	cases := []struct {
		status int
		header string
		class  classify.Class
		retry  time.Duration
	}{
		{http.StatusUnauthorized, "", classify.Auth, 0},
		{http.StatusForbidden, "", classify.Auth, 0},
		{http.StatusTooManyRequests, "2", classify.RateLimited, 2 * time.Second},
		{http.StatusInternalServerError, "", classify.Unavailable, 0},
		{http.StatusBadRequest, "", classify.BadRequest, 0},
		{http.StatusNotFound, "", classify.BadRequest, 0},
		{http.StatusTeapot, "", classify.BadRequest, 0},
	}
	for _, tc := range cases {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if tc.header != "" {
				w.Header().Set("Retry-After", tc.header)
			}
			w.WriteHeader(tc.status)
			io.WriteString(w, `{"title":"no"}`)
		}))
		c, err := New(srv.URL, "tok", srv.Client())
		if err != nil {
			t.Fatal(err)
		}
		_, err = c.ListAccounts(context.Background(), "")
		var ce *classify.ClassifiedError
		if !errors.As(err, &ce) || ce.Class != tc.class || ce.RetryAfter != tc.retry {
			t.Errorf("status %d: %v", tc.status, err)
		}
		if ce != nil && ce.Class == classify.IPBanned {
			t.Errorf("status %d produced ip_banned", tc.status)
		}
		srv.Close()
	}
}

func TestTransportErrorIsUnavailable(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	c, err := New(srv.URL, "tok", srv.Client())
	if err != nil {
		t.Fatal(err)
	}
	srv.Close()
	_, err = c.ListAccounts(context.Background(), "")
	if !classify.Is(err, classify.Unavailable) {
		t.Fatalf("%v", err)
	}
}

func TestNewRequiresSettings(t *testing.T) {
	if _, err := New("", "tok", nil); err == nil || !strings.Contains(err.Error(), "not set") {
		t.Fatalf("%v", err)
	}
	if _, err := New("http://example", "", nil); err == nil || !strings.Contains(err.Error(), "not set") {
		t.Fatalf("%v", err)
	}
}
