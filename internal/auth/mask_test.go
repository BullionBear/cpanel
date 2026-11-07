package auth

import "testing"

func TestMaskSecrets(t *testing.T) {
	raw, err := maskedJSON(map[string]any{
		"name":  "ci",
		"token": "cpanel_secret",
		"nested": map[string]any{
			"code": "abc",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	got := string(raw)
	if want := `{"name":"ci","nested":{"code":"***"},"token":"***"}`; got != want && got != `{"name":"ci","token":"***","nested":{"code":"***"}}` {
		// map order is not stable; check the pieces
	}
	for _, part := range []string{`"name":"ci"`, `"token":"***"`, `"code":"***"`} {
		if !contains(got, part) {
			t.Fatalf("%s missing from %s", part, got)
		}
	}
	if contains(got, "cpanel_secret") || contains(got, "abc") {
		t.Fatalf("secret left in %s", got)
	}
}

func contains(s, part string) bool {
	return len(s) >= len(part) && (s == part || len(part) == 0 || (len(s) > 0 && (find(s, part))))
}

func find(s, part string) bool {
	for i := 0; i+len(part) <= len(s); i++ {
		if s[i:i+len(part)] == part {
			return true
		}
	}
	return false
}
