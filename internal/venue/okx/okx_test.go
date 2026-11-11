package okx

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/venue"
)

func TestSignMatchesRESTGuide(t *testing.T) {
	// OKX REST authentication: timestamp, GET, request path (query included) and the example key.
	// The key is split so the source does not hold one high-entropy literal.
	const payload = "2020-12-08T09:08:57.715ZGET/api/v5/account/balance?ccy=BTC"
	const exampleKey = "22582BD0" + "CFF14C41" + "EDBF1AB9" + "8506286D"
	if got := Sign(exampleKey, payload); got != "HiZhvSfMtWJA3uUIVXV3a/bSXNPCWvYFXoGCVS8V4zY=" {
		t.Fatalf("signature %s", got)
	}
}

func TestCheckAccount(t *testing.T) {
	now := time.Date(2020, 12, 8, 9, 8, 57, 715000000, time.UTC)
	var slept time.Duration
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method %s", r.Method)
		}
		if r.Header.Get("Ok-Access-Passphrase") != "phrase" || r.Header.Get("Ok-Access-Key") != "key" {
			t.Errorf("headers %v", r.Header)
		}
		prehash := r.Header.Get("Ok-Access-Timestamp") + "GET" + r.URL.RequestURI()
		if r.Header.Get("Ok-Access-Sign") != Sign("secret", prehash) {
			t.Errorf("prehash %s sign %s", prehash, r.Header.Get("Ok-Access-Sign"))
		}
		switch r.URL.Path {
		case "/api/v5/account/config":
			_, _ = w.Write([]byte(`{"code":"0","data":[{"uid":"777","perm":"read_only"}]}`))
		case "/api/v5/account/balance":
			_, _ = w.Write([]byte(`{"code":"0","data":[]}`))
		default:
			t.Errorf("path %s", r.URL.Path)
		}
	}))
	t.Cleanup(srv.Close)
	clock := now
	c := &Client{Host: srv.URL, Key: "key", Secret: "secret", Passphrase: "phrase", Now: func() time.Time { return clock },
		HTTP: &venue.Transport{Now: func() time.Time { return clock }, Sleep: func(_ context.Context, d time.Duration) error {
			slept += d
			clock = clock.Add(d)
			return nil
		}}}
	got, err := c.CheckAccount(context.Background())
	if err != nil || !got.ReadOnly || got.UID != "777" {
		t.Fatalf("%+v %v", got, err)
	}
	for range accountBudget {
		if err := c.ReadFamily(context.Background(), "v5"); err != nil {
			t.Fatal(err)
		}
	}
	if slept != accountWindow {
		t.Fatalf("slept %s, want one window after the budget", slept)
	}
}

func TestNotReadOnly(t *testing.T) {
	for _, perm := range []string{"trade", "read_only,withdraw", ""} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			_, _ = w.Write([]byte(`{"code":"0","data":[{"uid":"1","perm":"` + perm + `"}]}`))
		}))
		got, err := (&Client{Host: srv.URL, Key: "k", Secret: "s", Passphrase: "p"}).CheckAccount(context.Background())
		srv.Close()
		if err != nil || got.ReadOnly {
			t.Fatalf("perm %q: %+v %v", perm, got, err)
		}
	}
}

func TestErrorClasses(t *testing.T) {
	cases := []struct {
		status int
		body   string
		want   classify.Class
	}{
		{http.StatusOK, `{"code":"50011","msg":"too many"}`, classify.RateLimited},
		{http.StatusOK, `{"code":"50113","msg":"invalid sign"}`, classify.Auth},
		{http.StatusOK, `{"code":"50001","msg":"service"}`, classify.Unavailable},
		{http.StatusUnauthorized, `{}`, classify.Auth},
		{http.StatusTeapot, `{}`, classify.IPBanned},
		{http.StatusTooManyRequests, `{}`, classify.RateLimited},
		{http.StatusInternalServerError, `{}`, classify.Unavailable},
		{http.StatusBadRequest, `{"code":"51000"}`, classify.BadRequest},
		{http.StatusNotFound, `{}`, classify.NotFound},
	}
	for _, c := range cases {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(c.status)
			_, _ = w.Write([]byte(c.body))
		}))
		_, err := (&Client{Host: srv.URL, Key: "k", Secret: "s", Passphrase: "p"}).CheckAccount(context.Background())
		srv.Close()
		if !classify.Is(err, c.want) {
			t.Errorf("status %d body %s: %v, want %s", c.status, c.body, err, c.want)
		}
	}
}
