// Package binance is the read-only Binance client (C5-03). Spot and SAPI share one host.
// USD-M futures use another. Signing is HMAC-SHA256 of the query string, as the spot
// REST docs specify. Only GET is sent.
package binance

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/venue"
)

const (
	spotHost    = "https://api.binance.com"
	futuresHost = "https://fapi.binance.com"
	recvWindow  = "5000"

	// WeightLimit is the spot REQUEST_WEIGHT budget per minute. WeightBudget is the
	// point at which the next call waits for the next minute (80%).
	WeightLimit  = 6000
	WeightBudget = 4800
)

// Client calls Binance with one key. Spot and Futures default to the public hosts.
type Client struct {
	Spot     string
	Futures  string
	Key      string
	Secret   string
	Now      func() time.Time
	HTTP     *venue.Transport
	requests int
}

// CheckAccount reads uid from GET /api/v3/account and key permissions from
// GET /sapi/v1/account/apiRestrictions. The account payload's permissions and
// canTrade fields describe the account, not the key, so they are ignored.
func (c *Client) CheckAccount(ctx context.Context) (venue.Check, error) {
	body, _, err := c.get(ctx, c.spot(), "/api/v3/account", "")
	if err != nil {
		return venue.Check{}, err
	}
	var account struct {
		UID json.Number `json:"uid"`
	}
	if err := unmarshal(body, &account); err != nil || account.UID.String() == "" {
		return venue.Check{}, &classify.ClassifiedError{Class: classify.BadRequest, Detail: "account response has no uid"}
	}
	body, _, err = c.get(ctx, c.spot(), "/sapi/v1/account/apiRestrictions", "")
	if err != nil {
		return venue.Check{}, err
	}
	ok, summary, reason := readOnly(body)
	return venue.Check{UID: account.UID.String(), ReadOnly: ok, Summary: summary, Reason: reason}, nil
}

// ReadFamily checks that an enabled kind can be read. api is the same call as the uid
// check, so the caller does not ask for it again. fapi is GET /fapi/v2/account.
// dapi, eapi and papi are not called.
func (c *Client) ReadFamily(ctx context.Context, family string) error {
	switch family {
	case "api":
		return nil
	case "fapi":
		_, _, err := c.get(ctx, c.futures(), "/fapi/v2/account", "")
		return err
	default:
		return &classify.ClassifiedError{Class: classify.BadRequest, Detail: "api family is not called"}
	}
}

// Calls is how many GET requests this client has sent.
func (c *Client) Calls() int { return c.requests }

func (c *Client) spot() string {
	if c.Spot != "" {
		return strings.TrimRight(c.Spot, "/")
	}
	return spotHost
}

func (c *Client) futures() string {
	if c.Futures != "" {
		return strings.TrimRight(c.Futures, "/")
	}
	return futuresHost
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

func (c *Client) get(ctx context.Context, host, path, query string) ([]byte, http.Header, error) {
	ts := strconv.FormatInt(c.now().UnixMilli(), 10)
	if query != "" {
		query += "&"
	}
	query += "recvWindow=" + recvWindow + "&timestamp=" + ts
	query += "&signature=" + sign(c.Secret, query)
	req, err := http.NewRequest(http.MethodGet, host+path+"?"+query, nil)
	if err != nil {
		return nil, nil, err
	}
	req.Header.Set("X-MBX-APIKEY", c.Key)
	status, header, body, err := c.http().Get(ctx, req)
	c.requests++
	c.noteWeight(header)
	if refined := refine(status, body, err); refined != nil {
		return body, header, refined
	}
	return body, header, nil
}

func (c *Client) http() *venue.Transport {
	if c.HTTP == nil {
		c.HTTP = &venue.Transport{}
	}
	return c.HTTP
}

func (c *Client) noteWeight(h http.Header) {
	if usedWeight(h) < WeightBudget {
		return
	}
	now := c.now()
	c.http().Hold(now.Truncate(time.Minute).Add(time.Minute))
}

func usedWeight(h http.Header) int {
	best := 0
	for k, vs := range h {
		if !strings.HasPrefix(strings.ToLower(k), "x-mbx-used-weight-") || len(vs) == 0 {
			continue
		}
		n, err := strconv.Atoi(vs[0])
		if err == nil && n > best {
			best = n
		}
	}
	return best
}

// sign is HMAC-SHA256 of query, hex encoded. query does not include the signature.
func sign(secret, query string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(query))
	return hex.EncodeToString(mac.Sum(nil))
}

// readOnly requires enableReading and rejects every other boolean that is not a read
// or an IP restriction. ipRestrict may be true. A missing enableReading is not read-only.
func readOnly(body []byte) (bool, string, string) {
	var fields map[string]any
	if err := json.Unmarshal(body, &fields); err != nil {
		return false, "", "apiRestrictions response is not an object"
	}
	reading, ok := fields["enableReading"].(bool)
	if !ok || !reading {
		return false, "enableReading=false", "enableReading is not true"
	}
	var extra []string
	for k, v := range fields {
		on, isBool := v.(bool)
		if !isBool || !on {
			continue
		}
		if k == "enableReading" || k == "ipRestrict" || k == "enableFixReadOnly" {
			continue
		}
		extra = append(extra, k)
	}
	sort.Strings(extra)
	if len(extra) > 0 {
		return false, "enableReading=true " + strings.Join(extra, " "), "not read-only: " + strings.Join(extra, ", ")
	}
	return true, "enableReading=true", ""
}

func refine(status int, body []byte, err error) error {
	if err == nil || status == http.StatusTeapot {
		return err
	}
	ce, ok := err.(*classify.ClassifiedError)
	if !ok || status == 0 {
		return err
	}
	var payload struct {
		Code int `json:"code"`
	}
	if json.Unmarshal(body, &payload) != nil || payload.Code == 0 {
		return err
	}
	switch payload.Code {
	case -1003, -1015:
		ce.Class = classify.RateLimited
	case -2014, -2015:
		ce.Class = classify.Auth
	}
	return ce
}

func unmarshal(body []byte, dest any) error {
	dec := json.NewDecoder(strings.NewReader(string(body)))
	dec.UseNumber()
	return dec.Decode(dest)
}

// SignQuery is the documented HMAC of a query string. Tests lock it to the spot docs.
func SignQuery(secret, query string) string { return sign(secret, query) }
