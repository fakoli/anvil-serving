"""Static user-command metadata shared by the two CLI entry points."""

# Shared with the product command registry so both entry points expose the same help.
USER_OPERATIONS = {
    "list": ("List local sign-in accounts (not Connect grants).", ()),
    "show": ("Show one local sign-in account (not Connect grants).", ()),
    "create": ("Create an account and request a password-setup email or private handoff.", ("email", "role", "grant", "output")),
    "access": ("Replace ALL browser grants, enable the account and revoke its Connect sessions.", ("grant",)),
    "suspend": ("Disable sign-in and revoke Connect browser and terminal sessions.", ()),
    "delete": ("Remove the account and factors; retain backups and disabled authority history.", ()),
    "reset-password": ("Request password setup; preserve groups, grants and registered factors.", ("output",)),
    "reset-mfa": ("Remove passkeys and TOTP; suspend first for a lost or compromised device.", ()),
    "code": ("Export a fresh enrollment code or setup link when using filesystem delivery.", ("output",)),
    "backup": ("Back up all authentication accounts and factors.", ("include-gateway",)),
    "schedule": ("Install the daily authentication and gateway backup timer.", ()),
    "deletion-schedule": ("Install the account-deletion processing timer.", ()),
    "process-deletions": ("Process one authorized permanent account deletion.", ()),
    "restore": ("Restore authentication backup into a fresh directory without activating it.", ("input", "sha256", "destination")),
}
