package api

import (
	"encoding/json"
	"net/http"

	cpanelv1 "github.com/BullionBear/cpanel/contract/gen/cpanel/v1"
)

func problemValue(status int, code, title, detail string) cpanelv1.Problem {
	p := cpanelv1.Problem{
		Type:   "about:blank",
		Title:  title,
		Status: status,
		Code:   code,
	}
	if detail != "" {
		p.Detail = &detail
	}
	return p
}

// writeProblem sends one application/problem+json body. detail is omitted when empty.
// The body never includes a database URL.
func writeProblem(w http.ResponseWriter, status int, code, title, detail string) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(problemValue(status, code, title, detail))
}
