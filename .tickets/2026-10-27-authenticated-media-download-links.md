# Authenticated media artifact download links

Media artifact inspection returned only a relative resource path. Agents on
another host could inspect a video but could not present its full download URL.

Bind the configured gateway origin into the existing request context. Return
`downloadUrl`, `requiresAuthentication` and `maxRangeBytes` for caller-owned
artifacts, plus native MCP resource links for MP4 output. Keep the eight-tool
surface, ownership, Bearer authentication, retention and HTTP range limits.
Cross-principal inspection and standalone contexts retain relative paths.

Verification covers configured origin versus an untrusted Host header, trailing
slashes, authenticated GET/Range, missing authentication, foreign ownership,
and assembly of an allowed artifact larger than one response. Clients fetch
with their existing credential reference; no credential is placed in the URL.
