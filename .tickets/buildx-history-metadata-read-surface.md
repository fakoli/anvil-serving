# Buildx history metadata read surface

A build client can terminate while daemon-side status remains uncertain. A bounded read-only command is needed to retrieve selected Buildx record metadata without invoking cache removal, reading build logs or returning build inputs and environment.

The supported `host docker-image build-history` surface binds one context, engine, single-node builder, producer revision and bounded time window. List-format fields and inspect-format fields are different producer interfaces: human-relative list timestamps and inspect-only template functions must not be assumed to match documentation examples. The fixed list JSON producer includes a name which is transiently parsed and discarded. Its evidence is limited to coarse record metadata; it does not establish build success, builder quiescence, exclusive ownership or daemon cleanup.
