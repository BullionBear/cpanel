package httpserver

import (
	"context"
	"io"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/rs/zerolog"
)

// serve starts Serve on a loopback port and returns its base URL, the function that stops it, and Serve's result.
func serve(t *testing.T, h http.Handler, shutdownTimeout time.Duration) (string, context.CancelFunc, <-chan error) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	done := make(chan error, 1)
	go func() { done <- Serve(ctx, zerolog.Nop(), ln, h, shutdownTimeout) }()
	return "http://" + ln.Addr().String(), cancel, done
}

func wait(t *testing.T, done <-chan error) error {
	t.Helper()
	select {
	case err := <-done:
		return err
	case <-time.After(10 * time.Second):
		t.Fatal("Serve did not return")
		return nil
	}
}

func TestHealthz(t *testing.T) {
	base, cancel, done := serve(t, NewMux(), time.Second)

	resp, err := http.Get(base + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || strings.TrimSpace(string(body)) != `{"ok":true}` {
		t.Errorf("GET /healthz = %d %q", resp.StatusCode, body)
	}
	if ct := resp.Header.Get("Content-Type"); ct != "application/json" {
		t.Errorf("Content-Type = %q", ct)
	}

	resp, err = http.Post(base+"/healthz", "text/plain", nil)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("POST /healthz = %d, want 405", resp.StatusCode)
	}

	cancel()
	if err := wait(t, done); err != nil {
		t.Errorf("Serve = %v, want nil after the context ends", err)
	}
}

func TestShutdownWaitsForInFlightRequests(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	h := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		close(entered)
		<-release
		w.WriteHeader(http.StatusNoContent)
	})
	base, cancel, done := serve(t, h, 10*time.Second)

	got := make(chan int, 1)
	go func() {
		resp, err := http.Get(base + "/")
		if err != nil {
			got <- -1
			return
		}
		resp.Body.Close()
		got <- resp.StatusCode
	}()
	<-entered
	cancel()

	select {
	case err := <-done:
		t.Fatalf("Serve returned %v with a request in flight", err)
	case <-time.After(100 * time.Millisecond):
	}
	close(release)
	if code := <-got; code != http.StatusNoContent {
		t.Errorf("in-flight request = %d, want 204", code)
	}
	if err := wait(t, done); err != nil {
		t.Errorf("Serve = %v", err)
	}
}

func TestShutdownTimeout(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	defer close(release)
	h := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		close(entered)
		<-release
	})
	base, cancel, done := serve(t, h, 50*time.Millisecond)

	go func() {
		if resp, err := http.Get(base + "/"); err == nil {
			resp.Body.Close()
		}
	}()
	<-entered
	cancel()
	if err := wait(t, done); err == nil || !strings.HasPrefix(err.Error(), "shutdown:") {
		t.Errorf("Serve = %v, want a shutdown error", err)
	}
}

func TestListenAndServeBadAddress(t *testing.T) {
	err := ListenAndServe(context.Background(), zerolog.Nop(), "127.0.0.1:not-a-port", NewMux(), time.Second)
	if err == nil {
		t.Fatal("ListenAndServe accepted a bad address")
	}
}
