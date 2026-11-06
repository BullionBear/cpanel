// Package httpserver runs a cpanel process's HTTP listener: /healthz while the process is up, and a graceful
// shutdown when the context ends. cpanel-api and cpanel-worker both use it.
package httpserver

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"time"

	"github.com/rs/zerolog"
)

// readHeaderTimeout bounds how long a client may take to send its request headers.
const readHeaderTimeout = 10 * time.Second

// NewMux returns a mux that serves GET /healthz.
func NewMux() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", Healthz)
	return mux
}

// Healthz answers 200 {"ok":true} while the process is up, as mftiko-api's /healthz does.
func Healthz(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"ok":true}` + "\n"))
}

// ListenAndServe is Serve on a new TCP listener at addr.
func ListenAndServe(ctx context.Context, log zerolog.Logger, addr string, h http.Handler, shutdownTimeout time.Duration) error {
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	return Serve(ctx, log, ln, h, shutdownTimeout)
}

// Serve serves h on ln until ctx ends. It then stops accepting, waits up to shutdownTimeout for in-flight requests,
// and returns nil. It returns an error if the server stops by itself or the requests outlast shutdownTimeout.
func Serve(ctx context.Context, log zerolog.Logger, ln net.Listener, h http.Handler, shutdownTimeout time.Duration) error {
	srv := &http.Server{Handler: h, ReadHeaderTimeout: readHeaderTimeout}
	served := make(chan error, 1)
	go func() { served <- srv.Serve(ln) }()
	log.Info().Str("addr", ln.Addr().String()).Msg("listening")

	select {
	case err := <-served:
		return fmt.Errorf("serve: %w", err)
	case <-ctx.Done():
	}

	log.Info().Str("timeout", shutdownTimeout.String()).Msg("shutting down")
	sctx, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
	defer cancel()
	if err := srv.Shutdown(sctx); err != nil {
		_ = srv.Close()
		return fmt.Errorf("shutdown: %w", err)
	}
	if err := <-served; !errors.Is(err, http.ErrServerClosed) {
		return fmt.Errorf("serve: %w", err)
	}
	return nil
}
