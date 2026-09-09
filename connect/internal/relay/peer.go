package relay

import (
	"crypto/x509"
	"encoding/asn1"
)

// ExactPeerName narrows standard CA/hostname verification for managed service
// identities. A wildcard or multi-purpose certificate is not a resource identity.
// Callers must separately require a verified chain and the proper TLS EKU.
func ExactPeerName(certificate *x509.Certificate, name string) bool {
	if certificate == nil || len(certificate.DNSNames) != 1 || certificate.DNSNames[0] != name || len(certificate.IPAddresses) != 0 || len(certificate.URIs) != 0 || len(certificate.EmailAddresses) != 0 {
		return false
	}
	count := 0
	for _, extension := range certificate.Extensions {
		if !extension.Id.Equal(asn1.ObjectIdentifier{2, 5, 29, 17}) {
			continue
		}
		count++
		// x509 exposes only selected GeneralName kinds. Check the signed DER
		// profile too, so an ignored otherName cannot make this multi-purpose.
		var sequence, identity asn1.RawValue
		rest, err := asn1.Unmarshal(extension.Value, &sequence)
		if err != nil || len(rest) != 0 || sequence.Class != 0 || sequence.Tag != 16 || !sequence.IsCompound {
			return false
		}
		rest, err = asn1.Unmarshal(sequence.Bytes, &identity)
		if err != nil || len(rest) != 0 || identity.Class != 2 || identity.Tag != 2 || identity.IsCompound || string(identity.Bytes) != name {
			return false
		}
	}
	return count == 1
}
