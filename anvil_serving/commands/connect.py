"""Explicit local deployment commands for Anvil Connect."""

from .family import command_family
from .spec import CommandNode, _handler, _node, _option


_MANIFEST = _option("--manifest", summary="Required local Connect deployment JSON.", value_name="PATH")
_SERVICE = _option("--service", summary="gateway, connector:ID, or client:ID in this manifest.", value_name="SERVICE")
# Mutation happens only when this option is present. Its conditional gate lets
# the same command produce a preview by default without prompting the operator.
_CONFIRM = (
    _option("--dry-run", summary="Preview without changing files or services."),
    _option("--confirm", summary="Apply the declared operation; otherwise preview.", requires_confirmation=True),
)


def _command(name: str, summary: str, *, service: bool = True, mutation: bool = False, extra: tuple = ()) -> CommandNode:
    return _node(
        name, summary,
        handler=_handler("anvil_serving.connect.cli", attribute="dispatch", argv_prefix=(name,), forward_confirm_flag=mutation),
        options=(_MANIFEST,) + ((_SERVICE,) if service else ()) + extra + (_CONFIRM if mutation else ()),
        mutation_class="mutate" if mutation else "read",
        # A Connect manifest owns its local deployment independently of model
        # topology. This surface cannot select remote controller/SSH targets.
        execution_policy="offline",
        docs_anchor=f"docs/cli/connect.md#{name}",
    )


@command_family(category="Anvil Connect")
def commands() -> CommandNode:
    return _node(
        "connect", "Manage authenticated API and browser access with Anvil Connect.",
        children=(
            _node(
                "qualify", "Run an isolated Connect qualification lane using saved local settings.",
                handler=_handler("anvil_serving.connect.cli", attribute="dispatch", argv_prefix=("qualify",)),
                options=(
                    _option("--prepare-container", summary="Explicitly download public pinned dependencies and prepare the local test image; exclusive with --lane."),
                    _option("--lane", summary="baseline, container-baseline or device; defaults to baseline.", value_name="LANE"),
                    _option("--config", summary="Private qualification TOML; defaults to ~/.config/anvil-connect/qualification.toml.", value_name="PATH"),
                ),
                mutation_class="process", execution_policy="offline",
                docs_anchor="docs/cli/connect.md#qualify",
            ),
            _command("validate", "Validate declarations and selected native components."),
            _command("render", "Preview or stage an owned configuration generation.", service=False, mutation=True),
            _command("up", "Preview or activate only the selected owned services.", mutation=True, extra=(
                _option("--services", summary="Comma-separated exact roles for one coordinated activation; exclusive with --service.", value_name="SERVICES"),
                _option("--upgrade", summary="Explicitly accept new artifact paths while retaining verified rollback binaries."),
            )),
            _command("down", "Preview or stop only the selected owned services.", mutation=True),
            _command("status", "Inspect owned service state without claiming origin readiness."),
            _command("doctor", "Check declared paths, components, and ownership."),
            _command("logs", "Read bounded service event metadata.", extra=(_option("--tail", summary="At most 200 recent events.", value_name="COUNT"),)),
            _command("init", "Initialize an authority or enroll a connector explicitly.", mutation=True, extra=(_option("--bundle", summary="Private invitation bundle for connector enrollment.", value_name="PATH"),)),
            _command("identity", "Read the public fingerprint of a declared connector."),
            _command("admin", "Send a declared request to the local gateway authority.", service=False, mutation=True, extra=(
                _option("--request", summary="Closed administrative JSON request file.", value_name="PATH"),
                _option("--output", summary="Exclusive private output for an issued credential.", value_name="PATH"),
            )),
            _command("keygen", "Create a private key for a declared local SDK forwarder.", mutation=True, extra=(_option("--output", summary="Exclusive private output for the local key.", value_name="PATH"),)),
            _command("backup", "Back up the stopped gateway authority to a private file.", service=False, mutation=True, extra=(
                _option("--output", summary="Exclusive private backup output.", value_name="PATH"),
            )),
            _command("restore", "Restore fenced authority into a fresh private directory.", service=False, mutation=True, extra=(
                _option("--input", summary="Private gateway backup file.", value_name="PATH"),
                _option("--destination", summary="Fresh gateway state directory; never activated automatically.", value_name="PATH"),
                _option("--sha256", summary="Backup digest retained independently at creation.", value_name="DIGEST"),
                _option("--native-sha256", summary="Approved native executable digest.", value_name="DIGEST"),
            )),
            _command("migration", "Preview Observatory access at one canonical origin.", service=False, extra=(
                _option("--observatory-config", summary="Existing Observatory access configuration.", value_name="PATH"),
                _option("--resource", summary="Exact browser resource ID in the manifest.", value_name="ID"),
            )),
            _command("edge-status", "Compare declared resources with the live Cloudflare edge.", service=False, extra=(
                _option("--edge-config", summary="Private edge-publishing configuration; defaults to the operator-home connect/edge-cloudflare.json.", value_name="PATH"),
            )),
            _command("edge-apply", "Apply declared DNS records and tunnel ingress rules through Cloudflare.", service=False, mutation=True, extra=(
                _option("--edge-config", summary="Private edge-publishing configuration; defaults to the operator-home connect/edge-cloudflare.json.", value_name="PATH"),
                _option("--retire-orphans", summary="Retire DNS and ingress state for hosts the declaration withdrew; the default reports them and leaves them routed."),
            )),
            _command("extend", "Extend one enrolled connector with newly declared resources.", mutation=True),
        ),
        docs_anchor="docs/cli/connect.md",
    )
