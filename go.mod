module github.com/BullionBear/cpanel

go 1.26.0

require (
	// v0.8.0 is commit c769041. Move it only in its own pull request (C2-D007); go.sum pins the content.
	github.com/BullionBear/mftiko v0.8.0
	github.com/jackc/pgx/v5 v5.11.0
	github.com/rs/zerolog v1.35.1
)

require (
	connectrpc.com/connect v1.21.1 // indirect
	github.com/apapsch/go-jsonmerge/v2 v2.0.0 // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/jackc/pgpassfile v1.0.0 // indirect
	github.com/jackc/pgservicefile v0.0.0-20240606120523-5a60cdf6a761 // indirect
	github.com/mattn/go-colorable v0.1.14 // indirect
	github.com/mattn/go-isatty v0.0.20 // indirect
	github.com/oapi-codegen/runtime v1.7.0 // indirect
	golang.org/x/sys v0.48.0 // indirect
	golang.org/x/text v0.42.0 // indirect
	google.golang.org/protobuf v1.36.12 // indirect
)
