// Package venue is the read-only REST port for Binance, Bybit and OKX (C5-03).
//
// It sends only GET. A key is used to read an account, never to place or move funds.
// cpanel-api and internal/reconcile must not import this package: the public process
// cannot call an exchange, and the ReconcileLoop only writes jobs.
//
// Errors are classify.ClassifiedError (C3-04). not_found is the status default; a later
// handler decides what a missing order means. HTTP 418 stays ip_banned, and the worker
// already pauses the egress for that class.
package venue

import (
	"context"
	"errors"
	"io"
	"net/http"
	"time"

	"github.com/BullionBear/cpanel/internal/classify"
)

// ErrMethod is returned when a caller tries to send anything other than GET.
// The request is not sent.
var ErrMethod = errors.New("venue: only GET is sent")

// maxBody is how much of a response is kept. The rest is discarded.
const maxBody = 1 << 20

// Check is what an account-level read learned. Reason is empty when ReadOnly is true.
// Neither field contains the api key or the secret.
type Check struct {
	UID      string
	ReadOnly bool
	Summary  string
	Reason   string
}

// Transport sends GET requests and can hold the next one until a rate-limit budget recovers.
type Transport struct {
	Client *http.Client
	Now    func() time.Time
	Sleep  func(context.Context, time.Duration) error

	until time.Time
}

// Hold keeps the next Get from starting until t. An earlier deadline is kept.
func (t *Transport) Hold(until time.Time) {
	if t == nil || !until.After(t.until) {
		return
	}
	t.until = until
}

// Get sends req. The method must be GET; anything else returns ErrMethod and sends nothing.
// A non-2xx status is a classified error. The body and header are still returned so the
// caller can read a weight or an exchange code. A transport failure is unavailable and
// does not quote the URL.
func (t *Transport) Get(ctx context.Context, req *http.Request) (int, http.Header, []byte, error) {
	if req == nil || req.Method != http.MethodGet {
		return 0, nil, nil, ErrMethod
	}
	if err := t.wait(ctx); err != nil {
		return 0, nil, nil, err
	}
	client := http.DefaultClient
	if t != nil && t.Client != nil {
		client = t.Client
	}
	res, err := client.Do(req.WithContext(ctx))
	if err != nil {
		return 0, nil, nil, &classify.ClassifiedError{Class: classify.Unavailable, Detail: "request failed"}
	}
	defer res.Body.Close()
	body, err := io.ReadAll(io.LimitReader(res.Body, maxBody))
	if err != nil {
		return res.StatusCode, res.Header, nil, &classify.ClassifiedError{Class: classify.Unavailable, Status: res.StatusCode, Detail: "response was not read"}
	}
	if res.StatusCode < 200 || res.StatusCode >= 300 {
		return res.StatusCode, res.Header, body, classify.FromHTTP(res.StatusCode, res.Header.Get("Retry-After"), body, t.now())
	}
	return res.StatusCode, res.Header, body, nil
}

func (t *Transport) wait(ctx context.Context) error {
	if t == nil || !t.until.After(t.now()) {
		return nil
	}
	d := t.until.Sub(t.now())
	if t.Sleep != nil {
		return t.Sleep(ctx, d)
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func (t *Transport) now() time.Time {
	if t != nil && t.Now != nil {
		return t.Now()
	}
	return time.Now()
}
