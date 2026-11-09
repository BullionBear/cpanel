package sym

import (
	"context"
	"crypto/tls"
	"errors"
	"net"
	"net/http"
	"strings"
	"testing"

	"connectrpc.com/connect"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"

	symv1 "github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1"
	"github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1/symv1connect"

	"github.com/BullionBear/cpanel/internal/classify"
)

type scriptedSym struct {
	symv1connect.UnimplementedSymServiceHandler
	token     string
	revisions func() *symv1.RevisionsResponse
	list      func(req *symv1.ListRequest) (*symv1.ListResponse, error)
	byUID     func(req *symv1.GetByUidRequest) (*symv1.GetByUidResponse, error)
	saw       []string
}

func (s *scriptedSym) Revisions(_ context.Context, req *connect.Request[symv1.RevisionsRequest]) (*connect.Response[symv1.RevisionsResponse], error) {
	s.saw = append(s.saw, req.Header().Get("Content-Type"))
	if req.Header().Get("Authorization") != "Bearer "+s.token {
		return nil, connect.NewError(connect.CodeUnauthenticated, errors.New("token"))
	}
	return connect.NewResponse(s.revisions()), nil
}

func (s *scriptedSym) List(_ context.Context, req *connect.Request[symv1.ListRequest]) (*connect.Response[symv1.ListResponse], error) {
	s.saw = append(s.saw, req.Header().Get("Content-Type"))
	if s.list == nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("no list"))
	}
	resp, err := s.list(req.Msg)
	if err != nil {
		return nil, err
	}
	return connect.NewResponse(resp), nil
}

func (s *scriptedSym) GetByUid(_ context.Context, req *connect.Request[symv1.GetByUidRequest]) (*connect.Response[symv1.GetByUidResponse], error) {
	if s.byUID == nil {
		return nil, connect.NewError(connect.CodeNotFound, errors.New("missing"))
	}
	resp, err := s.byUID(req.Msg)
	if err != nil {
		return nil, err
	}
	return connect.NewResponse(resp), nil
}

func grpcOnly(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Content-Type"), "application/grpc") {
			http.Error(w, "grpc only", http.StatusUnsupportedMediaType)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func serveH2C(t *testing.T, h http.Handler) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{Handler: h2c.NewHandler(h, &http2.Server{})}
	go func() { _ = srv.Serve(ln) }()
	t.Cleanup(func() { _ = srv.Close() })
	return "http://" + ln.Addr().String()
}

func TestClientUsesGRPC(t *testing.T) {
	size := 0.001
	tick := 0.1
	fake := &scriptedSym{
		token: "tok",
		revisions: func() *symv1.RevisionsResponse {
			return &symv1.RevisionsResponse{Revisions: &symv1.RevisionList{Tables: []*symv1.TableRevision{
				{Venue: "BinanceUM", Category: "Perp", Revision: 6},
			}}}
		},
		list: func(req *symv1.ListRequest) (*symv1.ListResponse, error) {
			if req.GetIncludeInactive() || req.GetLimit() != pageSize {
				t.Errorf("list %+v", req)
			}
			return &symv1.ListResponse{List: &symv1.SymbolList{
				Total: 1,
				Symbols: []*symv1.SymbolInfo{{
					Uid: 7, UniversalTicker: "BinanceUM_Perp_BTCUSDT", ExchTicker: "BTCUSDT",
					Base: "BTC", Quote: "USDT", ContractSize: &size, Status: symv1.SymbolStatus_SYMBOL_STATUS_TRADABLE,
					Filters:     map[string]*symv1.Bound{"price_tick": {Value: &tick}, "min_qty": {}},
					UpdatedAtNs: 1_700_000_000_000_000_000,
				}},
			}}, nil
		},
		byUID: func(req *symv1.GetByUidRequest) (*symv1.GetByUidResponse, error) {
			if !req.GetIncludeInactive() {
				t.Errorf("include_inactive false")
			}
			return &symv1.GetByUidResponse{Info: &symv1.SymbolInfo{
				Uid: req.GetUid(), UniversalTicker: "BinanceUM_Perp_ETHUSDT", ExchTicker: "ETHUSDT",
				Base: "ETH", Quote: "USDT", Status: symv1.SymbolStatus_SYMBOL_STATUS_DELISTED,
			}}, nil
		},
	}
	mux := http.NewServeMux()
	path, handler := symv1connect.NewSymServiceHandler(fake)
	mux.Handle(path, grpcOnly(handler))
	url := serveH2C(t, mux)
	client, err := New(url, "tok")
	if err != nil {
		t.Fatal(err)
	}
	tables, err := client.Revisions(context.Background())
	if err != nil || len(tables) != 1 || tables[0].Revision != 6 || tables[0].Venue != "BinanceUM" {
		t.Fatalf("%+v %v", tables, err)
	}
	page, err := client.List(context.Background(), "BinanceUM", "Perp", 0)
	if err != nil || len(page.Instruments) != 1 {
		t.Fatalf("%+v %v", page, err)
	}
	got := page.Instruments[0]
	if got.Venue != "BinanceUM" || got.Category != "Perp" || got.Status != "TRADABLE" || got.ContractSize == nil || *got.ContractSize != size {
		t.Fatalf("%+v", got)
	}
	_, hasMin := got.Filters["min_qty"]
	if got.Filters["price_tick"] == nil || *got.Filters["price_tick"] != tick || !hasMin || got.Filters["min_qty"] != nil {
		t.Fatalf("filters %+v", got.Filters)
	}
	inst, err := client.GetByUid(context.Background(), 9, true)
	if err != nil || inst.Status != "DELISTED" || inst.Venue != "BinanceUM" || inst.Category != "Perp" {
		t.Fatalf("%+v %v", inst, err)
	}
	for _, ct := range fake.saw {
		if !strings.HasPrefix(ct, "application/grpc") {
			t.Errorf("content-type %q", ct)
		}
	}
}

func TestClientClassifiesStatus(t *testing.T) {
	codes := []struct {
		code  connect.Code
		class classify.Class
	}{
		{connect.CodeUnauthenticated, classify.Auth},
		{connect.CodeInvalidArgument, classify.BadRequest},
		{connect.CodeNotFound, classify.NotFound},
		{connect.CodeUnavailable, classify.Unavailable},
		{connect.CodeDeadlineExceeded, classify.Unavailable},
		{connect.CodeResourceExhausted, classify.Unavailable},
		{connect.CodeInternal, classify.Unavailable},
	}
	for _, tc := range codes {
		t.Run(tc.code.String(), func(t *testing.T) {
			mux := http.NewServeMux()
			path, handler := symv1connect.NewSymServiceHandler(errSym{code: tc.code})
			mux.Handle(path, grpcOnly(handler))
			client, err := New(serveH2C(t, mux), "tok")
			if err != nil {
				t.Fatal(err)
			}
			_, err = client.Revisions(context.Background())
			if !classify.Is(err, tc.class) {
				t.Fatalf("%v", err)
			}
		})
	}
}

type errSym struct {
	symv1connect.UnimplementedSymServiceHandler
	code connect.Code
}

func (e errSym) Revisions(context.Context, *connect.Request[symv1.RevisionsRequest]) (*connect.Response[symv1.RevisionsResponse], error) {
	return nil, connect.NewError(e.code, errors.New("nope"))
}

func TestClientTransportError(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	url := "http://" + ln.Addr().String()
	ln.Close()
	client, err := New(url, "tok")
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.Revisions(context.Background())
	if !classify.Is(err, classify.Unavailable) {
		t.Fatalf("%v", err)
	}
}

func TestNewRequiresURLAndToken(t *testing.T) {
	if _, err := New("", "tok"); err == nil || !strings.Contains(err.Error(), "not set") {
		t.Fatalf("%v", err)
	}
	if _, err := New("http://127.0.0.1:1", ""); err == nil {
		t.Fatal("empty token")
	}
}

func TestConnectProtocolIsRefused(t *testing.T) {
	fake := &scriptedSym{token: "tok", revisions: func() *symv1.RevisionsResponse {
		return &symv1.RevisionsResponse{}
	}}
	mux := http.NewServeMux()
	path, handler := symv1connect.NewSymServiceHandler(fake)
	mux.Handle(path, grpcOnly(handler))
	url := serveH2C(t, mux)
	hc := &http.Client{Transport: &http2.Transport{
		AllowHTTP: true,
		DialTLSContext: func(ctx context.Context, network, addr string, _ *tls.Config) (net.Conn, error) {
			var d net.Dialer
			return d.DialContext(ctx, network, addr)
		},
	}}
	plain := symv1connect.NewSymServiceClient(hc, url) // Connect protocol, not gRPC
	_, err := plain.Revisions(context.Background(), connect.NewRequest(&symv1.RevisionsRequest{}))
	if err == nil {
		t.Fatal("connect protocol was accepted by a gRPC-only server")
	}
}
