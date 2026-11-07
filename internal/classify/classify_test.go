package classify

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestClassOfStatus(t *testing.T) {
	cases := []struct {
		status int
		want   Class
	}{
		{401, Auth},
		{403, Auth},
		{429, RateLimited},
		{418, IPBanned},
		{404, NotFound},
		{408, Unavailable},
		{500, Unavailable},
		{503, Unavailable},
		{400, BadRequest},
		{422, BadRequest},
		{302, Internal},
		{200, Internal}, // FromHTTP is only called for a failure; a 2xx is not a class
	}
	for _, c := range cases {
		if got := classOf(c.status); got != c.want {
			t.Errorf("status %d → %s, want %s", c.status, got, c.want)
		}
	}
}

func TestParseRetryAfter(t *testing.T) {
	now := time.Date(2026, 11, 8, 0, 0, 0, 0, time.UTC)
	cases := []struct {
		in   string
		want time.Duration
		ok   bool
	}{
		{"120", 120 * time.Second, true},
		{"  5", 5 * time.Second, true},
		{"0", 0, false},
		{"-3", 0, false},
		{"", 0, false},
		{"soon", 0, false},
		{now.Add(90 * time.Second).Format(http.TimeFormat), 90 * time.Second, true},
		{now.Add(-time.Second).Format(http.TimeFormat), 0, false},
	}
	for _, c := range cases {
		got, ok := ParseRetryAfter(c.in, now)
		if ok != c.ok || got != c.want {
			t.Errorf("ParseRetryAfter(%q) = %s, %v; want %s, %v", c.in, got, ok, c.want, c.ok)
		}
	}
}

func TestFromHTTPKeepsTheHeaderAndAShortBody(t *testing.T) {
	body := []byte(strings.Repeat("x", maxDetail+10))
	e := FromHTTP(429, "15", body, time.Now())
	if e.Class != RateLimited || e.Status != 429 || e.RetryAfter != 15*time.Second || len(e.Detail) != maxDetail {
		t.Fatalf("%+v", e)
	}
	if !Is(e, RateLimited) || Is(e, Auth) || Is(errors.New("nope"), "") {
		t.Fatal("Is mismatch")
	}
	if got := e.Error(); !strings.Contains(got, "rate_limited") || !strings.Contains(got, "HTTP 429") {
		t.Fatalf("Error() = %q", got)
	}
}

func TestDo(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/ok":
			w.WriteHeader(http.StatusOK)
			_, _ = io.WriteString(w, "fine")
		case "/slow":
			time.Sleep(200 * time.Millisecond)
		case "/ban":
			w.Header().Set("Retry-After", "30")
			w.WriteHeader(http.StatusTeapot)
			_, _ = io.WriteString(w, "banned")
		default:
			w.WriteHeader(http.StatusBadRequest)
		}
	}))
	t.Cleanup(srv.Close)
	client := srv.Client()

	t.Run("2xx returns the body", func(t *testing.T) {
		req, err := http.NewRequest(http.MethodGet, srv.URL+"/ok", nil)
		if err != nil {
			t.Fatal(err)
		}
		resp, err := Do(context.Background(), client, req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		if resp.StatusCode != 200 || string(b) != "fine" {
			t.Fatalf("%d %q", resp.StatusCode, b)
		}
	})

	t.Run("418 is an IP ban with Retry-After", func(t *testing.T) {
		req, err := http.NewRequest(http.MethodGet, srv.URL+"/ban", nil)
		if err != nil {
			t.Fatal(err)
		}
		_, err = Do(context.Background(), client, req)
		var ce *ClassifiedError
		if !errors.As(err, &ce) || ce.Class != IPBanned || ce.RetryAfter != 30*time.Second || ce.Detail != "banned" {
			t.Fatalf("%v", err)
		}
	})

	t.Run("a cancelled context is not an exchange failure", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+"/slow", nil)
		if err != nil {
			t.Fatal(err)
		}
		_, err = Do(ctx, client, req)
		if !errors.Is(err, context.Canceled) || Is(err, Unavailable) {
			t.Fatalf("%v", err)
		}
	})

	t.Run("a client timeout is unavailable", func(t *testing.T) {
		slow := *client
		slow.Timeout = 20 * time.Millisecond
		req, err := http.NewRequest(http.MethodGet, srv.URL+"/slow", nil)
		if err != nil {
			t.Fatal(err)
		}
		_, err = Do(context.Background(), &slow, req)
		if !Is(err, Unavailable) {
			t.Fatalf("%v", err)
		}
	})

	t.Run("a closed server is unavailable", func(t *testing.T) {
		down := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
		c := down.Client()
		url := down.URL
		down.Close()
		req, err := http.NewRequest(http.MethodGet, url, nil)
		if err != nil {
			t.Fatal(err)
		}
		_, err = Do(context.Background(), c, req)
		if !Is(err, Unavailable) {
			t.Fatalf("%v", err)
		}
	})
}
