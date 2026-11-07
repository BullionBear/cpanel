package auth

import "strings"

// secretKeys are JSON object keys whose values are replaced before an audit row is stored.
var secretKeys = map[string]struct{}{
	"token": {}, "secret": {}, "password": {}, "code": {},
	"authorization": {}, "session": {}, "cookie": {},
	"access_token": {}, "client_secret": {}, "bot_token": {}, "state": {},
}

const masked = "***"

func maskValue(v any) {
	switch x := v.(type) {
	case map[string]any:
		for k, child := range x {
			if _, ok := secretKeys[strings.ToLower(k)]; ok {
				x[k] = masked
				continue
			}
			maskValue(child)
		}
	case []any:
		for _, child := range x {
			maskValue(child)
		}
	}
}
