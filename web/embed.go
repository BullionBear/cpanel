// Package web is the SPA cpanel-api embeds (C2-D004). C2-05 replaces web/build
// with the SvelteKit build; the embed path stays.
package web

import "embed"

// Build is the SPA. index.html is the fallback for unknown paths.
//
//go:embed all:build
var Build embed.FS
