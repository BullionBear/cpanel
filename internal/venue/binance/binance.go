// Package binance is the read-only Binance client (C5-03). Spot and SAPI share one host.
// USD-M futures use another. Signing is HMAC-SHA256 of the query string, or Ed25519 of
// that same string when the key type is ed25519. Only GET is sent.
//
// Env testnet uses the public testnet hosts unless Spot or Futures is set. It does not
// call a host from this package's tests; those pass httptest URLs.
package binance

import (
	"context"
	"crypto/ed25519"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/platform/ed25519pem"
	"github.com/BullionBear/cpanel/internal/platform/exchangeenv"
	"github.com/BullionBear/cpanel/internal/venue"
)

const (
	spotHost       = "https://api.binance.com"
	futuresHost    = "https://fapi.binance.com"
	spotTestnet    = "https://testnet.binance.vision"
	futuresTestnet = "https://testnet.binancefuture.com"
	recvWindow     = "5000"
	testnetSummary = "testnet; read-only not checked"

	// WeightLimit is the spot REQUEST_WEIGHT budget per minute. WeightBudget is the
	// point at which the next call waits for the next minute (80%).
	WeightLimit  = 6000
	WeightBudget = 4800
)

// Client calls Binance with one key. Spot and Futures default to the public hosts
// for the client's Env. KeyType empty is hmac.
type Client struct {
	Spot     string
	Futures  string
	Key      string
	Secret   string
	KeyType  string
	Env      string
	Now      func() time.Time
	HTTP     *venue.Transport
	requests int
}

// CheckAccount reads uid from GET /api/v3/account and key permissions from
// GET /sapi/v1/account/apiRestrictions. The account payload's permissions and
// canTrade fields describe the account, not the key, so they are ignored.
//
// Testnet has neither endpoint. It reads GET /fapi/v2/account and reports the key
// usable. The registered uid is filled in by the caller. Read-only is not checked.
func (c *Client) CheckAccount(ctx context.Context) (venue.Check, error) {
	if c.Env == exchangeenv.Testnet {
		body, _, err := c.get(ctx, c.futures(), "/fapi/v2/account", "")
		if err != nil {
			return venue.Check{}, err
		}
		var obj map[string]json.RawMessage
		if err := json.Unmarshal(body, &obj); err != nil {
			return venue.Check{}, &classify.ClassifiedError{Class: classify.BadRequest, Detail: "fapi account response is not an object"}
		}
		return venue.Check{ReadOnly: true, Summary: testnetSummary}, nil
	}
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
	if c.Env == exchangeenv.Testnet {
		return spotTestnet
	}
	return spotHost
}

func (c *Client) futures() string {
	if c.Futures != "" {
		return strings.TrimRight(c.Futures, "/")
	}
	if c.Env == exchangeenv.Testnet {
		return futuresTestnet
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
	sig, err := sign(c.KeyType, c.Secret, query)
	if err != nil {
		return nil, nil, err
	}
	query += "&signature=" + url.QueryEscape(sig)
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

// sign is the signature of query. query does not include the signature.
// hmac is SHA256 hex. ed25519 is the raw signature, standard base64.
// An empty key type is hmac.
func sign(keyType, secret, query string) (string, error) {
	switch exchangeenv.KeyType(keyType) {
	case exchangeenv.KeyHMAC:
		mac := hmac.New(sha256.New, []byte(secret))
		_, _ = mac.Write([]byte(query))
		return hex.EncodeToString(mac.Sum(nil)), nil
	case exchangeenv.KeyEd25519:
		key, err := ed25519pem.PrivateKey(secret)
		if err != nil {
			return "", err
		}
		return base64.StdEncoding.EncodeToString(ed25519.Sign(key, []byte(query))), nil
	default:
		return "", &classify.ClassifiedError{Class: classify.BadRequest, Detail: "unknown key type"}
	}
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
func SignQuery(secret, query string) string {
	sig, err := sign(exchangeenv.KeyHMAC, secret, query)
	if err != nil {
		return ""
	}
	return sig
}

// SignEd25519 is the Ed25519 signature of payload, standard base64.
// payload is the query string for REST, or the FIX logon string in the official example.
func SignEd25519(pemText, payload string) (string, error) {
	return sign(exchangeenv.KeyEd25519, pemText, payload)
}
