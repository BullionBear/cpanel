// Package mftiko is the mftiko-api client (C4-01) and pins the rest of the mftiko contract cpanel builds
// against (C2-D007). go.mod requires one mftiko commit; the blank import keeps cloid through go mod tidy
// until C7-04 decodes it, so a breaking contract change fails the pull request that moves the pin.
// The SYM client lives in internal/sym.
//
// cpanel imports nothing of mftiko but contract/ (I1, internal/archtest). Only this package imports
// contract/gen/api/v1.
package mftiko

import (
	_ "github.com/BullionBear/mftiko/contract/wire/cloid"
)
