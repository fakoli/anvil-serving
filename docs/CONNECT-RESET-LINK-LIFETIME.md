# Password setup and reset link lifetime

Set `authelia.reset_link_lifespan_seconds` in the Connect deployment manifest
to control the lifetime of newly issued password setup and reset links:

```json
"reset_link_lifespan_seconds": 3600
```

The optional setting accepts an integer from 1 to 86400 seconds. Omitting it
preserves Authelia's five-minute default. It applies to all accounts; existing
tokens retain their original expiry. Enrollment verification codes have their
own lifetime. The filesystem notifier's five-minute retrieval window is also
unchanged.

Preview and apply with `anvil-serving connect up --manifest <deployment.json>
--service gateway`, adding `--confirm` to apply. Authelia validates the generated
`identity_validation.reset_password.jwt_lifespan` setting before activation.
