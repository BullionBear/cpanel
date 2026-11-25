package exchangeenv

import "testing"

func TestResolve(t *testing.T) {
	got, err := Resolve("", "")
	if err != nil || got != Prod {
		t.Fatalf("default %q %v", got, err)
	}
	got, err = Resolve(Prod, Allow)
	if err != nil || got != Prod {
		t.Fatalf("prod %q %v", got, err)
	}
	if _, err := Resolve(Testnet, ""); err == nil {
		t.Fatal("testnet without the allow flag")
	}
	if _, err := Resolve(Testnet, "true"); err == nil {
		t.Fatal("testnet with allow=true")
	}
	got, err = Resolve("  testnet  ", Allow)
	if err != nil || got != Testnet {
		t.Fatalf("testnet %q %v", got, err)
	}
	if _, err := Resolve("demo", Allow); err == nil {
		t.Fatal("accepted demo")
	}
}

func TestSameAndKeyType(t *testing.T) {
	if !Same("", Prod) || !Same(Testnet, Testnet) || Same(Prod, Testnet) {
		t.Fatal("Same")
	}
	if KeyType("") != KeyHMAC || KeyType(KeyHMAC) != KeyHMAC || KeyType(KeyEd25519) != KeyEd25519 {
		t.Fatal("KeyType")
	}
}
