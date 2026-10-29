# Buildx history metadata read surface

A build client can terminate while daemon-side status remains uncertain. A bounded read-only command is needed to retrieve selected Buildx record metadata without invoking cache removal, reading build logs or returning build inputs and environment.

The supported `host docker-image build-history` surface binds one context, engine, single-node builder and bounded time window, and can inspect one exact reference. Its evidence is limited to returned record metadata; it does not establish builder quiescence, exclusive ownership or daemon cleanup.
