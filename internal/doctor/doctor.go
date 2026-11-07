// Package doctor is cpanel-worker doctor (C2-07): it checks, from where the worker runs, that the worker reaches
// mftiko and SYM with its tokens. Every call is a read: GET /accounts on mftiko-api, grpc.health.v1 Check and
// Revisions on SYM. It changes nothing on either side.
package doctor

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"connectrpc.com/connect"

	apiv1 "github.com/BullionBear/mftiko/contract/gen/api/v1"
	symv1 "github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1"
	"github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1/symv1connect"
)

// Config is where mftiko and SYM are and the worker's tokens for them.
type Config struct {
	MftikoURL   string // mftiko-api base, e.g. http://100.65.26.119:8080/api/v1
	MftikoToken string
	SymURL      string // SYM's gRPC listener, e.g. http://100.65.26.119:7450
	SymToken    string
	HTTP        *http.Client // nil means a client with a 10s timeout
}

// Result is one check: what it called and either a short detail or the error.
type Result struct {
	Name   string
	Detail string
	Err    error
}

// OK reports whether the check passed.
func (r Result) OK() bool { return r.Err == nil }

// Run runs every check in order and returns all the results; a failure does not stop the later checks.
func Run(ctx context.Context, cfg Config) []Result {
	hc := cfg.HTTP
	if hc == nil {
		hc = &http.Client{Timeout: 10 * time.Second}
	}
	return []Result{
		check("mftiko GET /accounts", func() (string, error) { return mftikoAccounts(ctx, hc, cfg) }),
		check("sym grpc.health.v1 Check", func() (string, error) { return symHealth(ctx, hc, cfg) }),
		check("sym Revisions", func() (string, error) { return symRevisions(ctx, hc, cfg) }),
	}
}

// Write prints one line per result and reports whether all passed.
func Write(w io.Writer, results []Result) bool {
	ok := true
	for _, r := range results {
		if r.OK() {
			fmt.Fprintf(w, "ok    %s: %s\n", r.Name, r.Detail)
			continue
		}
		ok = false
		fmt.Fprintf(w, "FAIL  %s: %v\n", r.Name, r.Err)
	}
	return ok
}

func check(name string, f func() (string, error)) Result {
	detail, err := f()
	return Result{Name: name, Detail: detail, Err: err}
}

func bearer(token string) func(context.Context, *http.Request) error {
	return func(_ context.Context, req *http.Request) error {
		req.Header.Set("Authorization", "Bearer "+token)
		return nil
	}
}

func mftikoAccounts(ctx context.Context, hc *http.Client, cfg Config) (string, error) {
	if cfg.MftikoURL == "" || cfg.MftikoToken == "" {
		return "", errors.New("mftiko URL or token is not set")
	}
	c, err := apiv1.NewClientWithResponses(cfg.MftikoURL, apiv1.WithHTTPClient(hc), apiv1.WithRequestEditorFn(bearer(cfg.MftikoToken)))
	if err != nil {
		return "", err
	}
	limit := 1
	resp, err := c.ListAccountsWithResponse(ctx, &apiv1.ListAccountsParams{Limit: &limit})
	if err != nil {
		return "", err
	}
	if resp.JSON200 == nil {
		return "", fmt.Errorf("status %d: %s", resp.StatusCode(), snippet(resp.Body))
	}
	return fmt.Sprintf("200, %d account(s) on the first page of 1", len(resp.JSON200.Items)), nil
}

// symHealth calls grpc.health.v1.Health/Check with the Connect protocol's JSON encoding; it needs no token
// (mftiko E22-D014).
func symHealth(ctx context.Context, hc *http.Client, cfg Config) (string, error) {
	if cfg.SymURL == "" {
		return "", errors.New("SYM URL is not set")
	}
	body, _ := json.Marshal(map[string]string{"service": symv1connect.SymServiceName})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		strings.TrimRight(cfg.SymURL, "/")+"/grpc.health.v1.Health/Check", bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Connect-Protocol-Version", "1")
	resp, err := hc.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("status %d: %s", resp.StatusCode, snippet(raw))
	}
	var out struct{ Status string }
	if err := json.Unmarshal(raw, &out); err != nil {
		return "", fmt.Errorf("decode %s: %w", snippet(raw), err)
	}
	if out.Status != "SERVING" && out.Status != "SERVING_STATUS_SERVING" {
		return "", fmt.Errorf("status %s", out.Status)
	}
	return out.Status, nil
}

// symRevisions is the cheapest SymService call; it proves the token.
func symRevisions(ctx context.Context, hc *http.Client, cfg Config) (string, error) {
	if cfg.SymURL == "" || cfg.SymToken == "" {
		return "", errors.New("SYM URL or token is not set")
	}
	c := symv1connect.NewSymServiceClient(hc, cfg.SymURL)
	req := connect.NewRequest(&symv1.RevisionsRequest{})
	req.Header().Set("Authorization", "Bearer "+cfg.SymToken)
	resp, err := c.Revisions(ctx, req)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%d table(s)", len(resp.Msg.GetRevisions().GetTables())), nil
}

func snippet(b []byte) string {
	s := strings.TrimSpace(string(b))
	if len(s) > 200 {
		s = s[:200] + "…"
	}
	return s
}
