package api

import (
	"context"
	"net/http"
)

type jarKey struct{}

type jar struct {
	cookies []*http.Cookie
}

// withCookies remembers cookies added during the request and writes them
// before the status. The OpenAPI response types do not carry multiple Set-Cookie headers.
func withCookies(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		j := &jar{}
		ctx := context.WithValue(r.Context(), jarKey{}, j)
		next.ServeHTTP(&cookieWriter{ResponseWriter: w, jar: j}, r.WithContext(ctx))
	})
}

func addCookie(ctx context.Context, c *http.Cookie) {
	j, _ := ctx.Value(jarKey{}).(*jar)
	if j == nil {
		return
	}
	j.cookies = append(j.cookies, c)
}

type cookieWriter struct {
	http.ResponseWriter
	jar   *jar
	wrote bool
}

func (c *cookieWriter) WriteHeader(code int) {
	if c.wrote {
		return
	}
	c.wrote = true
	for _, ck := range c.jar.cookies {
		http.SetCookie(c.ResponseWriter, ck)
	}
	c.ResponseWriter.WriteHeader(code)
}

func (c *cookieWriter) Write(b []byte) (int, error) {
	if !c.wrote {
		c.WriteHeader(http.StatusOK)
	}
	return c.ResponseWriter.Write(b)
}
