package doctor

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"connectrpc.com/connect"

	symv1 "github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1"
	"github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1/symv1connect"
)

// fakeMftiko answers GET /api/v1/accounts for the right bearer token and records every method it sees.
func fakeMftiko(t *testing.T, token string, methods *[]string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*methods = append(*methods, r.Method+" "+r.URL.Path)
		if r.Header.Get("Authorization") != "Bearer "+token {
			w.Header().Set("Content-Type", "application/problem+json")
			w.WriteHeader(http.StatusUnauthorized)
			io.WriteString(w, `{"title":"unauthenticated","status":401}`)
			return
		}
		if r.URL.Path != "/api/v1/accounts" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"freshness":{"source":"primary"},"items":[]}`)
	}))
	t.Cleanup(srv.Close)
	return srv
}

type fakeSym struct {
	symv1connect.UnimplementedSymServiceHandler
	token string
}

func (f fakeSym) Revisions(_ context.Context, req *connect.Request[symv1.RevisionsRequest]) (*connect.Response[symv1.RevisionsResponse], error) {
	if req.Header().Get("Authorization") != "Bearer "+f.token {
		return nil, connect.NewError(connect.CodeUnauthenticated, nil)
	}
	return connect.NewResponse(&symv1.RevisionsResponse{Revisions: &symv1.RevisionList{Tables: []*symv1.TableRevision{{}, {}}}}), nil
}

func fakeSymServer(t *testing.T, token, health string) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.Handle(symv1connect.NewSymServiceHandler(fakeSym{token: token}))
	mux.HandleFunc("/grpc.health.v1.Health/Check", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"status":"`+health+`"}`)
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

func TestRunAllPass(t *testing.T) {
	var methods []string
	mf := fakeMftiko(t, "mt", &methods)
	sym := fakeSymServer(t, "st", "SERVING_STATUS_SERVING")
	results := Run(context.Background(), Config{MftikoURL: mf.URL + "/api/v1", MftikoToken: "mt", SymURL: sym.URL, SymToken: "st"})
	var out bytes.Buffer
	if !Write(&out, results) {
		t.Fatalf("not all passed:\n%s", out.String())
	}
	if !strings.Contains(out.String(), "2 table(s)") {
		t.Errorf("output %q", out.String())
	}
	for _, m := range methods {
		if !strings.HasPrefix(m, "GET ") {
			t.Errorf("doctor sent %s to mftiko; only GET is allowed (I2)", m)
		}
	}
}

func TestRunReportsEachFailure(t *testing.T) {
	var methods []string
	mf := fakeMftiko(t, "mt", &methods)
	sym := fakeSymServer(t, "st", "NOT_SERVING")
	results := Run(context.Background(), Config{MftikoURL: mf.URL + "/api/v1", MftikoToken: "wrong", SymURL: sym.URL, SymToken: "wrong"})
	if len(results) != 3 {
		t.Fatalf("%d results", len(results))
	}
	for _, r := range results {
		if r.OK() {
			t.Errorf("%s passed: %s", r.Name, r.Detail)
		}
	}
	var out bytes.Buffer
	if Write(&out, results) || strings.Count(out.String(), "FAIL") != 3 {
		t.Errorf("output %q", out.String())
	}
}

func TestRunWithoutSettings(t *testing.T) {
	for _, r := range Run(context.Background(), Config{}) {
		if r.OK() || !strings.Contains(r.Err.Error(), "not set") {
			t.Errorf("%s: %v", r.Name, r.Err)
		}
	}
}
