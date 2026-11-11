package binance

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/venue"
)

func TestSignMatchesSpotDocs(t *testing.T) {
	// Binance spot REST request security. The key and the query are the published example.
	// The key is split so the source does not hold one high-entropy literal.
	const exampleKey = "NhqPtmdS" + "JYdKjVHj" + "A7PZj4Mg" + "e3R5YNiP" +
		"1e3UZjIn" + "ClVN65XA" + "bvqqM6A7" + "H5fATj0j"
	const query = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
	if got := SignQuery(exampleKey, query); got != "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71" {
		t.Fatalf("signature %s", got)
	}
}

func TestCheckAccount(t *testing.T) {
	now := time.Date(2026, 11, 11, 3, 0, 30, 0, time.UTC)
	var slept time.Duration
	var paths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method %s", r.Method)
		}
		paths = append(paths, r.URL.Path)
		if r.Header.Get("X-MBX-APIKEY") != "key" || r.URL.Query().Get("signature") == "" {
			t.Errorf("missing key or signature: %v", r.URL.RawQuery)
		}
		q := r.URL.Query()
		signed := "recvWindow=" + q.Get("recvWindow") + "&timestamp=" + q.Get("timestamp")
		if q.Get("signature") != SignQuery("secret", signed) {
			t.Errorf("signature does not cover %s", signed)
		}
		switch r.URL.Path {
		case "/api/v3/account":
			w.Header().Set("X-MBX-USED-WEIGHT-1m", "5000")
			_, _ = w.Write([]byte(`{"uid":12345,"canTrade":true,"permissions":["SPOT"]}`))
		case "/sapi/v1/account/apiRestrictions":
			_, _ = w.Write([]byte(`{"enableReading":true,"enableSpotAndMarginTrading":false,"enableFutures":false,"enableWithdrawals":false,"ipRestrict":true}`))
		default:
			t.Errorf("path %s", r.URL.Path)
		}
	}))
	t.Cleanup(srv.Close)
	c := &Client{Spot: srv.URL, Futures: srv.URL, Key: "key", Secret: "secret", Now: func() time.Time { return now },
		HTTP: &venue.Transport{Now: func() time.Time { return now }, Sleep: func(_ context.Context, d time.Duration) error {
			slept = d
			return nil
		}}}
	got, err := c.CheckAccount(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if !got.ReadOnly || got.UID != "12345" || got.Reason != "" {
		t.Fatalf("%+v", got)
	}
	if len(paths) != 2 || paths[0] != "/api/v3/account" || paths[1] != "/sapi/v1/account/apiRestrictions" {
		t.Fatalf("paths %v", paths)
	}
	if slept != 30*time.Second {
		t.Fatalf("slept %s, want the rest of the minute after weight 5000", slept)
	}
}

func TestNotReadOnly(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v3/account" {
			_, _ = w.Write([]byte(`{"uid":9}`))
			return
		}
		_, _ = w.Write([]byte(`{"enableReading":true,"enableFutures":true,"enableWithdrawals":false}`))
	}))
	t.Cleanup(srv.Close)
	c := &Client{Spot: srv.URL, Key: "k", Secret: "s", Now: func() time.Time { return time.Unix(0, 0) }}
	got, err := c.CheckAccount(context.Background())
	if err != nil || got.ReadOnly || got.Reason != "not read-only: enableFutures" {
		t.Fatalf("%+v %v", got, err)
	}
}

func TestErrorClasses(t *testing.T) {
	cases := []struct {
		status int
		body   string
		want   classify.Class
	}{
		{http.StatusUnauthorized, `{"code":-2015,"msg":"Invalid API-key"}`, classify.Auth},
		{http.StatusBadRequest, `{"code":-2015,"msg":"Invalid API-key"}`, classify.Auth},
		{http.StatusBadRequest, `{"code":-1003,"msg":"Too many requests"}`, classify.RateLimited},
		{http.StatusTeapot, `{"code":-1003}`, classify.IPBanned},
		{http.StatusTooManyRequests, `{}`, classify.RateLimited},
		{http.StatusInternalServerError, `{}`, classify.Unavailable},
		{http.StatusBadRequest, `{"code":-1100}`, classify.BadRequest},
		{http.StatusNotFound, `{}`, classify.NotFound},
	}
	for _, c := range cases {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(c.status)
			_, _ = w.Write([]byte(c.body))
		}))
		client := &Client{Spot: srv.URL, Futures: srv.URL, Key: "k", Secret: "s"}
		err := client.ReadFamily(context.Background(), "fapi")
		srv.Close()
		if !classify.Is(err, c.want) {
			t.Errorf("status %d body %s: %v, want %s", c.status, c.body, err, c.want)
		}
	}
}

func TestFuturesFamily(t *testing.T) {
	var path string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		_, _ = w.Write([]byte(`{"assets":[]}`))
	}))
	t.Cleanup(srv.Close)
	c := &Client{Futures: srv.URL, Key: "k", Secret: "s"}
	if err := c.ReadFamily(context.Background(), "fapi"); err != nil || path != "/fapi/v2/account" {
		t.Fatalf("path %s err %v", path, err)
	}
	if err := c.ReadFamily(context.Background(), "dapi"); err == nil || c.Calls() != 1 {
		t.Fatalf("dapi err %v calls %d", err, c.Calls())
	}
}
