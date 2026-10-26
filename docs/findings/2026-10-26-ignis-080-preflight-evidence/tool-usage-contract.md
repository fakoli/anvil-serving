# Ignis 0.8 tool-choice contract used by the follow-up

The follow-up used Ignis 0.8.0 source at peeled commit
`950ee0d1c2cf6178cd7fec29df3f17c3e127f987`. This is source interpretation,
not a substitute for the retained local behavior evidence.

- `auto` is the default and leaves the call decision to the model. `required`
  forces a tool-call opener; a named function additionally forces the name.
  Unknown choice shapes, `required` without tools, and a named function absent
  from the supplied tools fail with a 400.
  [Implementation](https://github.com/gpillon/ignis/blob/950ee0d1c2cf6178cd7fec29df3f17c3e127f987/crates/server/src/api.rs#L1312-L1445) ·
  [user guide](https://github.com/gpillon/ignis/blob/950ee0d1c2cf6178cd7fec29df3f17c3e127f987/docs/user/api/chat-completions.md#L74-L89)
- The forced prefix does not schema-constrain the model-generated arguments;
  the local `tool-call/v2-exact-weather` oracle still had to require one
  complete `get_weather` call whose JSON arguments contained
  `city: "Oakland"` and whose finish reason was `tool_calls`.
  [Design boundary](https://github.com/gpillon/ignis/blob/950ee0d1c2cf6178cd7fec29df3f17c3e127f987/docs/specs/server/11-forcing-a-tool-call.md#L250-L259)
- Thinking defaults on. The request may disable it with `enable_thinking`; with
  thinking enabled the forced opener acts after the reasoning close, while
  with thinking disabled it acts from the first generated token.
  [Thinking controls](https://github.com/gpillon/ignis/blob/950ee0d1c2cf6178cd7fec29df3f17c3e127f987/crates/server/src/thinking.rs#L17-L68) ·
  [forcing design](https://github.com/gpillon/ignis/blob/950ee0d1c2cf6178cd7fec29df3f17c3e127f987/docs/specs/server/11-forcing-a-tool-call.md#L286-L330)
- The `rtxpro6000` profile changes context, vision, speculative decoding,
  lanes, and n-gram settings. It does not set tool choice, thinking effort, or
  chat-template policy.
  [Profile source](https://github.com/gpillon/ignis/blob/950ee0d1c2cf6178cd7fec29df3f17c3e127f987/crates/server/src/config/profile.rs#L216-L227)

The phase-two policy is intentionally narrow: use `required` on a request when
that step must call a supplied tool. It is not a production-wide default for
coding, chat, or autonomous-agent traffic. The passing seeded `auto` cell is a
single mechanism observation; it neither rescored the earlier unseeded 3/4
batch nor established automatic-choice reliability.
