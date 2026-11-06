// Package mftiko pins the mftiko contract cpanel builds against (C2-D007). go.mod requires one mftiko commit; the
// imports below keep that requirement through go mod tidy and compile the contract packages cpanel will use with
// cpanel's toolchain, so a breaking contract change fails the pull request that moves the pin. The mftiko API
// client (C4-01), the SYM client (C4-02) and the cloid decoding (C7-04) replace them.
//
// cpanel imports nothing of mftiko but contract/ (I1, internal/archtest).
package mftiko

import (
	_ "github.com/BullionBear/mftiko/contract/gen/api/v1"
	_ "github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1"
	_ "github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1/symv1connect"
	_ "github.com/BullionBear/mftiko/contract/wire/cloid"
)
