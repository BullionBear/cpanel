// Package okx is the read-only OKX v5 client (C5-03). Signing follows the REST guide:
// base64(HMAC-SHA256(secret, timestamp + method + requestPath + body)). GET has an empty
// body, and the query string is part of requestPath. The passphrase is a header, not
// part of the prehash. Only GET is sent.
package okx

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/venue"
)

const (
	defaultHost = "https://www.okx.com"
	// accountBudget is under the account endpoint's 10 requests / 2s, on purpose.
	accountBudget = 4
	accountWindow = 2 * time.Second
)

// Client calls OKX with one key. Passphrase is the value sealed with the key.
type Client struct {
	Host       string
	Key        string
	Secret     string
	Passphrase string
	Now        func() time.Time
	HTTP       *venue.Transport
	requests   int
	budget     window
}

// CheckAccount reads GET /api/v5/account/config. uid must be present and perm must be read-only.
func (c *Client) CheckAccount(ctx context.Context) (venue.Check, error) {
	body, err := c.get(ctx, "/api/v5/account/config", "")
	if err != nil {
		return venue.Check{}, err
	}
	var payload struct {
		Data []struct {
			UID  string `json:"uid"`
			Perm string `json:"perm"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &payload); err != nil || len(payload.Data) == 0 || payload.Data[0].UID == "" {
		return venue.Check{}, &classify.ClassifiedError{Class: classify.BadRequest, Detail: "account config has no uid"}
	}
	row := payload.Data[0]
	if !readOnly(row.Perm) {
		reason := "api key is not read-only"
		if strings.TrimSpace(row.Perm) == "" {
			reason = "permission is missing"
		}
		return venue.Check{UID: row.UID, Summary: "perm=" + row.Perm, Reason: reason}, nil
	}
	return venue.Check{UID: row.UID, ReadOnly: true, Summary: "perm=" + row.Perm}, nil
}

// ReadFamily checks GET /api/v5/account/balance for the v5 family. Other families are not called.
func (c *Client) ReadFamily(ctx context.Context, family string) error {
	if family != "v5" {
		return &classify.ClassifiedError{Class: classify.BadRequest, Detail: "api family is not called"}
	}
	_, err := c.get(ctx, "/api/v5/account/balance", "")
	return err
}

// Calls is how many GET requests this client has sent.
func (c *Client) Calls() int { return c.requests }

func (c *Client) get(ctx context.Context, path, query string) ([]byte, error) {
	requestPath := path
	if query != "" {
		requestPath += "?" + query
	}
	if until := c.budget.reserve(c.now()); !until.IsZero() {
		c.http().Hold(until)
	}
	ts := c.now().UTC().Format("2006-01-02T15:04:05.000Z")
	req, err := http.NewRequest(http.MethodGet, c.host()+requestPath, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("OK-ACCESS-KEY", c.Key)
	req.Header.Set("OK-ACCESS-SIGN", sign(c.Secret, ts+"GET"+requestPath))
	req.Header.Set("OK-ACCESS-TIMESTAMP", ts)
	req.Header.Set("OK-ACCESS-PASSPHRASE", c.Passphrase)
	status, _, body, err := c.http().Get(ctx, req)
	c.requests++
	if refined := refine(status, body, err); refined != nil {
		return body, refined
	}
	return body, nil
}

func (c *Client) host() string {
	if c.Host != "" {
		return strings.TrimRight(c.Host, "/")
	}
	return defaultHost
}

func (c *Client) now() time.Time {
	if c.Now != nil {
		return c.Now()
	}
	if c.HTTP != nil && c.HTTP.Now != nil {
		return c.HTTP.Now()
	}
	return time.Now()
}

func (c *Client) http() *venue.Transport {
	if c.HTTP == nil {
		c.HTTP = &venue.Transport{Now: c.Now}
	}
	return c.HTTP
}

func sign(secret, payload string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(payload))
	return base64.StdEncoding.EncodeToString(mac.Sum(nil))
}

// Sign is the documented base64 HMAC of timestamp + method + requestPath + body.
func Sign(secret, payload string) string { return sign(secret, payload) }

// readOnly accepts a permission that is only read. trade and withdraw are rejected.
// An empty permission is not read-only: the key has to say what it can do.
func readOnly(perm string) bool {
	perm = strings.ToLower(strings.TrimSpace(perm))
	if perm == "" || strings.Contains(perm, "trade") || strings.Contains(perm, "withdraw") {
		return false
	}
	return strings.Contains(perm, "read")
}

func refine(status int, body []byte, err error) error {
	if err != nil && (status == 0 || status == http.StatusTeapot) {
		return err
	}
	var payload struct {
		Code string `json:"code"`
		Msg  string `json:"msg"`
	}
	_ = json.Unmarshal(body, &payload)
	if err == nil && (payload.Code == "" || payload.Code == "0") {
		return nil
	}
	if err == nil {
		return &classify.ClassifiedError{Class: codeClass(payload.Code), Status: status, Detail: payload.Msg}
	}
	ce, ok := err.(*classify.ClassifiedError)
	if !ok || payload.Code == "" || payload.Code == "0" {
		return err
	}
	ce.Class = codeClass(payload.Code)
	return ce
}

func codeClass(code string) classify.Class {
	switch code {
	case "50011":
		return classify.RateLimited
	case "50111", "50113", "50114":
		return classify.Auth
	case "50001", "50013":
		return classify.Unavailable
	default:
		return classify.BadRequest
	}
}

// window counts requests and tells the caller to wait once the budget is spent.
type window struct {
	mu    sync.Mutex
	start time.Time
	n     int
}

func (w *window) reserve(now time.Time) time.Time {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.start.IsZero() || now.Sub(w.start) >= accountWindow {
		w.start = now
		w.n = 0
	}
	if w.n >= accountBudget {
		return w.start.Add(accountWindow)
	}
	w.n++
	return time.Time{}
}
