# One image, three commands. The default is cpanel-api. Override the command to run another binary:
#   docker run --rm ghcr.io/bullionbear/cpanel /usr/local/bin/cpanel-worker
#   docker run --rm ghcr.io/bullionbear/cpanel /usr/local/bin/cpanel-migrate up
# The base has no shell. The process runs as nonroot.

FROM golang:1.26.0 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ARG VERSION=dev
ARG COMMIT=
ARG BUILT_AT=
RUN CGO_ENABLED=0 go build -trimpath \
	-ldflags "-s -w -X 'github.com/BullionBear/cpanel/internal/platform/buildinfo.Version=${VERSION}' -X 'github.com/BullionBear/cpanel/internal/platform/buildinfo.Commit=${COMMIT}' -X 'github.com/BullionBear/cpanel/internal/platform/buildinfo.BuiltAt=${BUILT_AT}'" \
	-o /out/ ./cmd/cpanel-api ./cmd/cpanel-worker ./cmd/cpanel-migrate

FROM gcr.io/distroless/static-debian12
COPY --from=build /out/cpanel-api /out/cpanel-worker /out/cpanel-migrate /usr/local/bin/
USER nonroot:nonroot
CMD ["/usr/local/bin/cpanel-api"]
