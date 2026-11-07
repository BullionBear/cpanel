package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/rs/zerolog"
)

type requestIDKey struct{}

func requestIDFrom(ctx context.Context) string {
	id, _ := ctx.Value(requestIDKey{}).(string)
	return id
}

func newRequestID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return strconv.FormatInt(time.Now().UnixNano(), 16)
	}
	return hex.EncodeToString(b[:])
}

// wrap applies the middleware from the outside in: request id, access log, timeout, recovery.
func wrap(log zerolog.Logger, timeout time.Duration, next http.Handler) http.Handler {
	h := recoverPanic(log, next)
	h = requestTimeout(log, timeout, h)
	h = accessLog(log, h)
	h = requestID(h)
	return h
}

// requestID sets X-Request-Id. A value sent by the client is ignored.
func requestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := newRequestID()
		w.Header().Set("X-Request-Id", id)
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), requestIDKey{}, id)))
	})
}

func accessLog(log zerolog.Logger, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sw := &statusWriter{ResponseWriter: w}
		start := time.Now()
		next.ServeHTTP(sw, r)
		status := sw.status
		if status == 0 {
			status = http.StatusOK
		}
		log.Info().
			Str("request_id", requestIDFrom(r.Context())).
			Str("method", r.Method).
			Str("path", r.URL.Path).
			Int("status", status).
			Int64("duration_ms", time.Since(start).Milliseconds()).
			Msg("request")
	})
}

// requestTimeout bounds a request. The handler runs in its own goroutine so a panic
// recovered inside it still finishes the response, and a timeout writes problem+json
// instead of hanging. Zero or negative disables the limit.
func requestTimeout(log zerolog.Logger, d time.Duration, next http.Handler) http.Handler {
	if d <= 0 {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), d)
		defer cancel()
		rec := newMemWriter()
		done := make(chan struct{})
		go func() {
			defer func() {
				if p := recover(); p != nil {
					log.Error().Str("request_id", requestIDFrom(ctx)).Str("panic", fmt.Sprint(p)).Msg("panic")
					if rec.code == 0 {
						writeProblem(rec, http.StatusInternalServerError, "internal", "Internal Server Error", "")
					}
				}
				close(done)
			}()
			next.ServeHTTP(rec, r.WithContext(ctx))
		}()
		select {
		case <-done:
			rec.flush(w)
		case <-ctx.Done():
			writeProblem(w, http.StatusGatewayTimeout, "timeout", "Request Timeout", "")
		}
	})
}

func recoverPanic(log zerolog.Logger, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sw := &statusWriter{ResponseWriter: w}
		defer func() {
			p := recover()
			if p == nil {
				return
			}
			log.Error().Str("request_id", requestIDFrom(r.Context())).Str("panic", fmt.Sprint(p)).Msg("panic")
			if !sw.wrote {
				writeProblem(w, http.StatusInternalServerError, "internal", "Internal Server Error", "")
			}
		}()
		next.ServeHTTP(sw, r)
	})
}

type statusWriter struct {
	http.ResponseWriter
	status int
	wrote  bool
}

func (s *statusWriter) WriteHeader(code int) {
	if s.wrote {
		return
	}
	s.wrote = true
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

func (s *statusWriter) Write(b []byte) (int, error) {
	if !s.wrote {
		s.WriteHeader(http.StatusOK)
	}
	return s.ResponseWriter.Write(b)
}

// memWriter buffers one response so a timeout can discard it.
type memWriter struct {
	header http.Header
	code   int
	body   []byte
}

func newMemWriter() *memWriter {
	return &memWriter{header: make(http.Header)}
}

func (m *memWriter) Header() http.Header { return m.header }

func (m *memWriter) WriteHeader(code int) {
	if m.code == 0 {
		m.code = code
	}
}

func (m *memWriter) Write(b []byte) (int, error) {
	if m.code == 0 {
		m.code = http.StatusOK
	}
	m.body = append(m.body, b...)
	return len(b), nil
}

func (m *memWriter) flush(w http.ResponseWriter) {
	for k, vs := range m.header {
		w.Header()[k] = append([]string(nil), vs...)
	}
	code := m.code
	if code == 0 {
		code = http.StatusOK
	}
	w.WriteHeader(code)
	_, _ = w.Write(m.body)
}

// methods lets the listed methods through. Anything else is problem+json.
func methods(next http.Handler, allowed ...string) http.Handler {
	allow := strings.Join(allowed, ", ")
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		for _, m := range allowed {
			if r.Method == m {
				next.ServeHTTP(w, r)
				return
			}
		}
		w.Header().Set("Allow", allow)
		writeProblem(w, http.StatusMethodNotAllowed, "method_not_allowed", "Method Not Allowed", "")
	})
}

// getOnly lets GET through and answers HEAD without a body. Anything else is problem+json.
func getOnly(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			next.ServeHTTP(w, r)
		case http.MethodHead:
			r2 := r.Clone(r.Context())
			r2.Method = http.MethodGet
			next.ServeHTTP(bodyDiscard{w}, r2)
		default:
			w.Header().Set("Allow", "GET, HEAD")
			writeProblem(w, http.StatusMethodNotAllowed, "method_not_allowed", "Method Not Allowed", "")
		}
	})
}

type bodyDiscard struct{ http.ResponseWriter }

func (bodyDiscard) Write(p []byte) (int, error) { return len(p), nil }
