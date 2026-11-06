package logger

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	"github.com/rs/zerolog"
)

func TestNewWritesJSONWithComponent(t *testing.T) {
	var buf bytes.Buffer
	log := New(&buf, "cpanel-test", DefaultLevel)
	log.Info().Msg("hello")
	log.Debug().Msg("hidden at info")

	lines := strings.Split(strings.TrimSpace(buf.String()), "\n")
	if len(lines) != 1 {
		t.Fatalf("want one line, got %q", buf.String())
	}
	var line map[string]any
	if err := json.Unmarshal([]byte(lines[0]), &line); err != nil {
		t.Fatalf("not one JSON object: %v: %q", err, lines[0])
	}
	for _, key := range []string{"time", "level", "component", "message"} {
		if _, ok := line[key]; !ok {
			t.Errorf("missing %q in %v", key, line)
		}
	}
	if line["component"] != "cpanel-test" || line["level"] != "info" || line["message"] != "hello" {
		t.Errorf("line %v", line)
	}
}

func TestLevelSet(t *testing.T) {
	ok := map[string]zerolog.Level{
		"trace":  zerolog.TraceLevel,
		"debug":  zerolog.DebugLevel,
		" Info ": zerolog.InfoLevel,
		"WARN":   zerolog.WarnLevel,
		"error":  zerolog.ErrorLevel,
	}
	for in, want := range ok {
		var l Level
		if err := l.Set(in); err != nil || zerolog.Level(l) != want {
			t.Errorf("Set(%q) = %v, %v; want %v", in, l, err, want)
		}
	}
	for _, in := range []string{"", "1", "fatal", "panic", "disabled", "verbose"} {
		l := DefaultLevel
		if err := l.Set(in); err == nil {
			t.Errorf("Set(%q) accepted", in)
		}
		if l != DefaultLevel {
			t.Errorf("Set(%q) changed the level to %v", in, l)
		}
	}
}
