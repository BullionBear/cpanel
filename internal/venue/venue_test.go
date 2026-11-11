package venue

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/BullionBear/cpanel/internal/classify"
)

func TestOnlyGETIsSent(t *testing.T) {
	hits := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { hits++ }))
	t.Cleanup(srv.Close)
	req, err := http.NewRequest(http.MethodPost, srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	_, _, _, err = (&Transport{}).Get(context.Background(), req)
	if err != ErrMethod || hits != 0 {
		t.Fatalf("err %v hits %d", err, hits)
	}
}

func TestStatusClasses(t *testing.T) {
	now := time.Date(2026, 11, 11, 0, 0, 0, 0, time.UTC)
	cases := []struct {
		status int
		want   classify.Class
	}{
		{http.StatusUnauthorized, classify.Auth},
		{http.StatusForbidden, classify.Auth},
		{http.StatusTooManyRequests, classify.RateLimited},
		{http.StatusTeapot, classify.IPBanned},
		{http.StatusInternalServerError, classify.Unavailable},
		{http.StatusBadRequest, classify.BadRequest},
		{http.StatusNotFound, classify.NotFound},
	}
	for _, c := range cases {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodGet {
				t.Errorf("method %s", r.Method)
			}
			w.WriteHeader(c.status)
		}))
		req, err := http.NewRequest(http.MethodGet, srv.URL, nil)
		if err != nil {
			t.Fatal(err)
		}
		_, _, _, err = (&Transport{Now: func() time.Time { return now }}).Get(context.Background(), req)
		srv.Close()
		if !classify.Is(err, c.want) {
			t.Errorf("status %d: %v, want %s", c.status, err, c.want)
		}
	}
}

func TestHoldWaits(t *testing.T) {
	var slept time.Duration
	now := time.Date(2026, 11, 11, 1, 0, 0, 0, time.UTC)
	tr := &Transport{
		Now:   func() time.Time { return now },
		Sleep: func(context.Context, time.Duration) error { return nil },
	}
	tr.Sleep = func(_ context.Context, d time.Duration) error {
		slept = d
		now = now.Add(d)
		return nil
	}
	tr.Hold(now.Add(1500 * time.Millisecond))
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	t.Cleanup(srv.Close)
	req, err := http.NewRequest(http.MethodGet, srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, _, err := tr.Get(context.Background(), req); err != nil {
		t.Fatal(err)
	}
	if slept != 1500*time.Millisecond {
		t.Fatalf("slept %s", slept)
	}
}
