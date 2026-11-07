package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/rs/zerolog"
)

func TestRequestIDIgnoresClient(t *testing.T) {
	h := handler(t, nil, "")
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	req.Header.Set("X-Request-Id", "from-client")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	got := rec.Header().Get("X-Request-Id")
	if got == "" || got == "from-client" || len(got) != 32 {
		t.Fatalf("request id %q", got)
	}
	for _, c := range got {
		if !strings.ContainsRune("0123456789abcdef", c) {
			t.Fatalf("request id %q", got)
		}
	}
}

func TestAccessLog(t *testing.T) {
	var buf strings.Builder
	log := zerolog.New(&buf)
	h := Handler(Config{Log: log, Timeout: time.Second})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	var line struct {
		Level     string `json:"level"`
		Message   string `json:"message"`
		RequestID string `json:"request_id"`
		Method    string `json:"method"`
		Path      string `json:"path"`
		Status    int    `json:"status"`
		Duration  int64  `json:"duration_ms"`
	}
	if err := json.Unmarshal([]byte(strings.TrimSpace(buf.String())), &line); err != nil {
		t.Fatalf("%v\n%s", err, buf.String())
	}
	if line.Level != "info" || line.Message != "request" || line.Method != http.MethodGet || line.Path != "/healthz" || line.Status != http.StatusOK || line.Duration < 0 {
		t.Fatalf("%+v", line)
	}
	if line.RequestID != rec.Header().Get("X-Request-Id") || line.RequestID == "" {
		t.Fatalf("log id %q header %q", line.RequestID, rec.Header().Get("X-Request-Id"))
	}
}

func TestPanicIsProblem(t *testing.T) {
	var buf strings.Builder
	log := zerolog.New(&buf)
	h := wrap(log, time.Second, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic("boom")
	}))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/x", nil))
	problemOf(t, rec, http.StatusInternalServerError, "internal")
	if !strings.Contains(buf.String(), "boom") {
		t.Fatalf("log %s", buf.String())
	}
}

func TestTimeoutIsProblem(t *testing.T) {
	block := make(chan struct{})
	t.Cleanup(func() { close(block) })
	h := wrap(zerolog.Nop(), 40*time.Millisecond, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		<-block
	}))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/x", nil))
	problemOf(t, rec, http.StatusGatewayTimeout, "timeout")
}
