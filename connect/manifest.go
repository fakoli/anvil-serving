// Package connect embeds the reviewed native transport pin for runtime checks.
package connect

import _ "embed"

//go:embed transport.lock.json
var transportManifest []byte

func TransportManifest() []byte { return append([]byte(nil), transportManifest...) }
