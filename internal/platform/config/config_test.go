package config

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/rs/zerolog"
)

type settings struct {
	listen  string
	token   string
	timeout time.Duration
}

func parse(t *testing.T, env map[string]string, args ...string) (settings, *Set, error) {
	t.Helper()
	var s settings
	set := New("test", func(k string) string { return env[k] })
	set.String(&s.listen, "listen", "T_LISTEN", ":8080", "listen address")
	set.Secret(&s.token, "token", "T_TOKEN", "bearer token")
	set.Duration(&s.timeout, "timeout", "T_TIMEOUT", 10*time.Second, "shutdown timeout")
	err := set.Parse(args)
	return s, set, err
}

// dict renders Set.Dict the way the startup log does.
func dict(t *testing.T, set *Set) map[string]string {
	t.Helper()
	var buf bytes.Buffer
	log := zerolog.New(&buf)
	log.Info().Dict("config", set.Dict()).Send()
	var line struct{ Config map[string]string }
	if err := json.Unmarshal(buf.Bytes(), &line); err != nil {
		t.Fatalf("log line %q: %v", buf.String(), err)
	}
	return line.Config
}

func TestPrecedence(t *testing.T) {
	cases := []struct {
		name string
		env  map[string]string
		args []string
		want settings
	}{
		{"defaults", nil, nil, settings{listen: ":8080", timeout: 10 * time.Second}},
		{"environment", map[string]string{"T_LISTEN": ":9000", "T_TOKEN": "s3cret", "T_TIMEOUT": "3s"}, nil,
			settings{listen: ":9000", token: "s3cret", timeout: 3 * time.Second}},
		{"flag over environment", map[string]string{"T_LISTEN": ":9000", "T_TIMEOUT": "3s"}, []string{"-listen", ":7000", "-timeout=1m"},
			settings{listen: ":7000", timeout: time.Minute}},
		{"flag set to the default still wins", map[string]string{"T_LISTEN": ":9000"}, []string{"-listen=:8080"},
			settings{listen: ":8080", timeout: 10 * time.Second}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, _, err := parse(t, c.env, c.args...)
			if err != nil {
				t.Fatal(err)
			}
			if got != c.want {
				t.Errorf("got %+v, want %+v", got, c.want)
			}
		})
	}
}

func TestBadEnvironmentNamesTheVariable(t *testing.T) {
	_, _, err := parse(t, map[string]string{"T_TIMEOUT": "soon"})
	if err == nil || !strings.HasPrefix(err.Error(), `$T_TIMEOUT: invalid value "soon"`) {
		t.Fatalf("err = %v", err)
	}
}

func TestBadEnvironmentIgnoredWhenTheFlagIsGiven(t *testing.T) {
	got, _, err := parse(t, map[string]string{"T_TIMEOUT": "soon"}, "-timeout=2s")
	if err != nil || got.timeout != 2*time.Second {
		t.Fatalf("got %+v, %v", got, err)
	}
}

func TestPositionalArgumentRejected(t *testing.T) {
	if _, _, err := parse(t, nil, "up"); err == nil || !strings.Contains(err.Error(), `"up"`) {
		t.Fatalf("err = %v", err)
	}
}

func TestDictMasksSecrets(t *testing.T) {
	_, set, err := parse(t, map[string]string{"T_TOKEN": "s3cret"}, "-listen=:9000")
	if err != nil {
		t.Fatal(err)
	}
	got := dict(t, set)
	want := map[string]string{"listen": ":9000", "token": Masked, "timeout": "10s"}
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("%s = %q, want %q", k, got[k], v)
		}
	}

	var buf bytes.Buffer
	log := zerolog.New(&buf)
	log.Info().Dict("config", set.Dict()).Send()
	if strings.Contains(buf.String(), "s3cret") {
		t.Errorf("secret in the log: %s", buf.String())
	}
	if i, j, k := strings.Index(buf.String(), `"listen"`), strings.Index(buf.String(), `"token"`), strings.Index(buf.String(), `"timeout"`); !(i < j && j < k) {
		t.Errorf("settings not in registration order: %s", buf.String())
	}
}

func TestDictShowsAnUnsetSecretAsEmpty(t *testing.T) {
	_, set, err := parse(t, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := dict(t, set)["token"]; got != "" {
		t.Errorf("token = %q, want empty", got)
	}
}
