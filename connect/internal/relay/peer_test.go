package relay

import (
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"testing"
)

func TestServiceCertificateHasOnlyOneExactDNSIdentity(t *testing.T) {
	const name = "gateway.anvil-connect.internal"
	dns := asn1.RawValue{Class: 2, Tag: 2, Bytes: []byte(name)}
	for _, kind := range []string{"exact", "wildcard", "multiple-dns", "ignored-othername", "duplicate-extension", "trailing-data"} {
		t.Run(kind, func(t *testing.T) {
			names := []asn1.RawValue{dns}
			if kind == "multiple-dns" {
				names = append(names, asn1.RawValue{Class: 2, Tag: 2, Bytes: []byte("other.anvil-connect.internal")})
			}
			if kind == "ignored-othername" {
				names = append(names, asn1.RawValue{Class: 2, Tag: 0, IsCompound: true, Bytes: []byte{5, 0}})
			}
			encoded, err := asn1.Marshal(names)
			if err != nil {
				t.Fatal(err)
			}
			if kind == "trailing-data" {
				encoded = append(encoded, 0)
			}
			certificate := &x509.Certificate{DNSNames: []string{name}, Extensions: []pkix.Extension{{Id: asn1.ObjectIdentifier{2, 5, 29, 17}, Value: encoded}}}
			if kind == "wildcard" {
				certificate.DNSNames = []string{"*.anvil-connect.internal"}
			}
			if kind == "duplicate-extension" {
				certificate.Extensions = append(certificate.Extensions, certificate.Extensions[0])
			}
			if ExactPeerName(certificate, name) != (kind == "exact") {
				t.Fatal("unexpected service certificate admission")
			}
		})
	}
}
