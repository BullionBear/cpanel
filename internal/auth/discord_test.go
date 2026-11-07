package auth

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestMemberSendsTheBotTokenAsAHeader(t *testing.T) {
	const bot = "bot-token-value"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.RawQuery != "" || strings.Contains(r.URL.RequestURI(), bot) {
			t.Errorf("bot token leaked into the URL %s", r.URL.RequestURI())
		}
		if r.Header.Get("Authorization") != "Bot "+bot {
			t.Errorf("authorization %q", r.Header.Get("Authorization"))
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"roles":["role-1"]}`)
	}))
	t.Cleanup(srv.Close)
	c := NewClient(Settings{APIBase: srv.URL, BotToken: bot, GuildID: "guild", RoleID: "role-1"})
	if err := c.Member(context.Background(), "42"); err != nil {
		t.Fatal(err)
	}
}
