// Package mftiko is the mftiko-api client (C4-01) and pins the rest of the mftiko contract cpanel builds
// against (C2-D007). go.mod requires one mftiko commit; the blank imports keep the packages this client does
// not use yet through go mod tidy, so a breaking contract change fails the pull request that moves the pin.
// The SYM client (C4-02) and the cloid decoding (C7-04) replace those two.
//
// cpanel imports nothing of mftiko but contract/ (I1, internal/archtest). Only this package imports
// contract/gen/api/v1.
package mftiko

import (
	_ "github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1"
	_ "github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1/symv1connect"
	_ "github.com/BullionBear/mftiko/contract/wire/cloid"
)
