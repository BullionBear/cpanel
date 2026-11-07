package openapi

import (
	"net/http"
	"testing"

	cpanelv1 "github.com/BullionBear/cpanel/contract/gen/cpanel/v1"
)

// TestServerIsStdHTTP locks the codegen settings: a strict server on net/http's ServeMux.
// Dropping a generated method fails the build; a different router fails here.
func TestServerIsStdHTTP(t *testing.T) {
	h := cpanelv1.Handler(cpanelv1.NewStrictHandler(nil, nil))
	if _, ok := h.(*http.ServeMux); !ok {
		t.Fatalf("handler type %T, want *http.ServeMux", h)
	}
}
