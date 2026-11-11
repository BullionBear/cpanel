package bybit

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/venue"
)

func TestSignMatchesV5Guide(t *testing.T) {
	// The v5 guide's GET prehash. The published signature omits the secret; the sample
	// script uses this secret for the same prehash, and the hex is its HMAC-SHA256.
	const payload = "1658384314791XXXXXXXXXX5000category=option&symbol=BTC-29JUL22-25000-C"
	if got := Sign("XXXXXXXXXX", payload); got != "c00720f96c5934ca7057ac28ae65b823f83b8b67a8fe784e7795ca0fa3c148ec" {
		t.Fatalf("signature %s", got)
	}
}

func TestCheckAccount(t *testing.T) {
	now := time.UnixMilli(1658384314791)
	var slept time.Duration
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method %s", r.Method)
		}
		if r.Header.Get("X-Bapi-Api-Key") != "XXXXXXXXXX" || r.Header.Get("X-Bapi-Recv-Window") != "5000" {
			t.Errorf("headers %v", r.Header)
		}
		payload := r.Header.Get("X-Bapi-Timestamp") + "XXXXXXXXXX" + "5000" + r.URL.RawQuery
		if r.Header.Get("X-Bapi-Sign") != Sign("secret", payload) {
			t.Errorf("sign payload %s", payload)
		}
		w.Header().Set("X-Bapi-Limit", "10")
		w.Header().Set("X-Bapi-Limit-Status", "1")
		w.Header().Set("X-Bapi-Limit-Reset-Timestamp", "1658384315791")
		switch r.URL.Path {
		case "/v5/user/query-api":
			_, _ = w.Write([]byte(`{"retCode":0,"retMsg":"OK","result":{"userID":4242,"readOnly":1}}`))
		case "/v5/account/wallet-balance":
			if r.URL.Query().Get("accountType") != "UNIFIED" {
				t.Errorf("accountType %s", r.URL.RawQuery)
			}
			_, _ = w.Write([]byte(`{"retCode":0,"result":{"list":[]}}`))
		default:
			t.Errorf("path %s", r.URL.Path)
		}
	}))
	t.Cleanup(srv.Close)
	c := &Client{Host: srv.URL, Key: "XXXXXXXXXX", Secret: "secret", Now: func() time.Time { return now },
		HTTP: &venue.Transport{Now: func() time.Time { return now }, Sleep: func(_ context.Context, d time.Duration) error {
			slept += d
			return nil
		}}}
	got, err := c.CheckAccount(context.Background())
	if err != nil || !got.ReadOnly || got.UID != "4242" {
		t.Fatalf("%+v %v", got, err)
	}
	if err := c.ReadFamily(context.Background(), "v5"); err != nil {
		t.Fatal(err)
	}
	if slept != time.Second {
		t.Fatalf("slept %s, want the reset delay after one request of ten remained", slept)
	}
}

func TestNotReadOnly(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"retCode":0,"result":{"userID":1,"readOnly":0}}`))
	}))
	t.Cleanup(srv.Close)
	got, err := (&Client{Host: srv.URL, Key: "k", Secret: "s"}).CheckAccount(context.Background())
	if err != nil || got.ReadOnly || got.Reason != "api key is not read-only" {
		t.Fatalf("%+v %v", got, err)
	}
}

func TestErrorClasses(t *testing.T) {
	cases := []struct {
		status int
		body   string
		want   classify.Class
	}{
		{http.StatusOK, `{"retCode":10006,"retMsg":"too many"}`, classify.RateLimited},
		{http.StatusOK, `{"retCode":10005,"retMsg":"permission"}`, classify.Auth},
		{http.StatusUnauthorized, `{"retCode":10003,"retMsg":"key"}`, classify.Auth},
		{http.StatusTeapot, `{}`, classify.IPBanned},
		{http.StatusTooManyRequests, `{}`, classify.RateLimited},
		{http.StatusInternalServerError, `{}`, classify.Unavailable},
		{http.StatusBadRequest, `{"retCode":10001}`, classify.BadRequest},
		{http.StatusNotFound, `{}`, classify.NotFound},
	}
	for _, c := range cases {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(c.status)
			_, _ = w.Write([]byte(c.body))
		}))
		_, err := (&Client{Host: srv.URL, Key: "k", Secret: "s"}).CheckAccount(context.Background())
		srv.Close()
		if !classify.Is(err, c.want) {
			t.Errorf("status %d body %s: %v, want %s", c.status, c.body, err, c.want)
		}
	}
}
