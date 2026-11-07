// Package classify turns an HTTP response into one of the job error classes (C3-04). A venue client maps the
// exchange's own codes onto ClassifiedError; FromHTTP is the default for a status code alone. Binance -2013, which
// arrives as HTTP 400, is the venue client's to mark not_found.
package classify

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// Class is an ingest.error_class value.
type Class string

const (
	Auth        Class = "auth"
	RateLimited Class = "rate_limited"
	IPBanned    Class = "ip_banned"
	Unavailable Class = "unavailable"
	BadRequest  Class = "bad_request"
	NotFound    Class = "not_found"
	Internal    Class = "internal"
)

// Known reports whether c is one of the classes the database accepts.
func (c Class) Known() bool {
	switch c {
	case Auth, RateLimited, IPBanned, Unavailable, BadRequest, NotFound, Internal:
		return true
	default:
		return false
	}
}

// maxDetail is how much of a response body an error keeps.
const maxDetail = 512

// ClassifiedError is a failed call to an exchange. RetryAfter is zero when the response had no usable Retry-After.
type ClassifiedError struct {
	Class      Class
	Status     int
	RetryAfter time.Duration
	Detail     string
}

func (e *ClassifiedError) Error() string {
	switch {
	case e == nil:
		return "classify: nil error"
	case e.Status == 0:
		return fmt.Sprintf("%s: %s", e.Class, e.Detail)
	case e.Detail == "":
		return fmt.Sprintf("%s: HTTP %d", e.Class, e.Status)
	default:
		return fmt.Sprintf("%s: HTTP %d: %s", e.Class, e.Status, e.Detail)
	}
}

// Is reports whether err carries a ClassifiedError of class. A nil class matches any ClassifiedError.
func Is(err error, class Class) bool {
	var ce *ClassifiedError
	if !errors.As(err, &ce) {
		return false
	}
	return class == "" || ce.Class == class
}

// FromHTTP classifies an HTTP status. retryAfter is the Retry-After header, and now interprets an HTTP-date in it.
// body is kept, truncated, as the error's detail.
func FromHTTP(status int, retryAfter string, body []byte, now time.Time) *ClassifiedError {
	after, _ := ParseRetryAfter(retryAfter, now)
	detail := strings.TrimSpace(string(body))
	if len(detail) > maxDetail {
		detail = detail[:maxDetail]
	}
	return &ClassifiedError{Class: classOf(status), Status: status, RetryAfter: after, Detail: detail}
}

// classOf is the default status mapping.
func classOf(status int) Class {
	switch status {
	case http.StatusUnauthorized, http.StatusForbidden:
		return Auth
	case http.StatusTooManyRequests:
		return RateLimited
	case http.StatusTeapot: // Binance uses 418 for an IP ban
		return IPBanned
	case http.StatusNotFound:
		return NotFound
	case http.StatusRequestTimeout:
		return Unavailable
	}
	switch {
	case status >= 500 && status <= 599:
		return Unavailable
	case status >= 400 && status <= 499:
		return BadRequest
	default:
		return Internal
	}
}

// ParseRetryAfter parses a Retry-After header: delta-seconds, or an HTTP-date relative to now. The ok result is
// false when the header is missing, zero, negative, already past, or not a number or a date.
func ParseRetryAfter(v string, now time.Time) (time.Duration, bool) {
	v = strings.TrimSpace(v)
	if v == "" {
		return 0, false
	}
	if n, err := strconv.Atoi(v); err == nil {
		if n <= 0 {
			return 0, false
		}
		return time.Duration(n) * time.Second, true
	}
	t, err := http.ParseTime(v)
	if err != nil {
		return 0, false
	}
	d := t.Sub(now)
	if d <= 0 {
		return 0, false
	}
	return d, true
}

// Do sends req with client. A 2xx response is returned for the caller to close. Anything else is a
// ClassifiedError, and the body is closed here. A transport error is unavailable, except when ctx has ended: that
// error is returned as ctx gave it, so a worker that is stopping does not record an exchange failure.
func Do(ctx context.Context, client *http.Client, req *http.Request) (*http.Response, error) {
	if client == nil {
		return nil, errors.New("classify: nil http client")
	}
	resp, err := client.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, &ClassifiedError{Class: Unavailable, Detail: err.Error()}
	}
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return resp, nil
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, maxDetail))
	return nil, FromHTTP(resp.StatusCode, resp.Header.Get("Retry-After"), body, time.Now())
}
