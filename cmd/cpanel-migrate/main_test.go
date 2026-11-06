package main

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/BullionBear/cpanel/internal/cmdtest"
)

func TestBinary(t *testing.T) {
	bin := cmdtest.Build(t)

	t.Run("logs its settings and exits 0", func(t *testing.T) {
		code, stderr := cmdtest.Run(t, bin, []string{"CPANEL_LOG_LEVEL=debug"})
		if code != 0 {
			t.Fatalf("exit %d, stderr %q", code, stderr)
		}
		var start struct {
			Component, Message string
			Config             map[string]string
		}
		first, _, _ := strings.Cut(stderr, "\n")
		if err := json.Unmarshal([]byte(first), &start); err != nil {
			t.Fatalf("first line %q: %v", first, err)
		}
		if start.Component != name || start.Message != "starting" || start.Config["log-level"] != "debug" {
			t.Errorf("startup line %q", first)
		}
	})

	t.Run("bad setting exits 2", func(t *testing.T) {
		code, stderr := cmdtest.Run(t, bin, nil, "-log-level=loud")
		if code != 2 || !strings.Contains(stderr, "-log-level") {
			t.Errorf("exit %d, stderr %q", code, stderr)
		}
	})
}
