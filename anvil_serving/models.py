"""`anvil-serving models` — catalog + fetch verbs for local model management.

Two sub-actions:
  * ``sync`` — scan HF caches + model dirs, pull cards, build the catalog (-> `_sync.py`).
  * ``pull`` — download a Hugging Face repo INTO A NAMED DOCKER VOLUME so it's ready
    to serve natively (see ``pull_main`` / ``build_pull_argv`` below).
  * ``recipe`` — manage recorded serve recipes (list/show/create/update/delete/load);
    benchmark ``--recipe-out`` remains the evidence-producing generate path.

Why ``pull`` mounts a NAMED VOLUME and not a host ``C:/…`` path (CLAUDE.md gotcha #15):
on this Windows + WSL2 + Docker box, serving weights from a ``C:/…`` bind mount reads
over 9P (~15 MB/s → 18–90 min cold loads); a named docker volume is ext4-native inside
the WSL2 VM (no 9P) and loads in seconds. So we download the repo straight into the
volume, then serve later with the repo-id as ``--model`` — bytes never touch 9P.
"""
import os
import argparse
import glob
from contextlib import contextmanager
from importlib import resources
import json
import shlex
import shutil
import subprocess
import tempfile
import tomllib
import sys
from . import config
from . import guard
from . import paths
from . import serve_recipes
HERE = os.path.dirname(__file__)

# `pull` defaults. The vLLM nightly image ships the `hf` CLI (huggingface_hub), so
# the download runs INSIDE it with the named volume mounted at the HF cache.
DEFAULT_PULL_VOLUME = "vllm-hfcache"
DEFAULT_PULL_IMAGE = "vllm/vllm-openai:nightly"
DEFAULT_PULL_TOKEN_ENV = "HF_TOKEN"
DEFAULT_PULL_TOKEN_FILE = "~/.env"
# Where the HF cache lives inside the container — the volume is mounted here so
# downloaded blobs land on native ext4, not a 9P bind mount (gotcha #15).
HF_CACHE_MOUNTPOINT = "/root/.cache/huggingface"
DEFAULT_CATALOG_DIR = "model-library"


class CatalogNotFound(FileNotFoundError):
    """Raised when a generated model catalog cannot be found."""

    def __init__(self, catalog_dir):
        super().__init__("model catalog not found: %s" % catalog_dir)
        self.catalog_dir = catalog_dir


class CatalogError(Exception):
    """Raised when catalog summary JSON exists but cannot be read."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def is_real_catalog_entry(entry):
    """Mirror ``_sync.is_real_model_row`` without importing the sync script.

    ``_sync.py`` has import-time output-directory side effects, so the read-only
    catalog path keeps the shared predicate duplicated here intentionally.
    """

    if entry.get("owner") == "unslothai":
        return False
    if (entry.get("size_gb") or 0) < 0.2 and entry.get("format") == "?":
        return False
    return bool(entry.get("model_type")) or entry.get("format") in ("safetensors", "GGUF")


def build_pull_argv(repo_id, volume=DEFAULT_PULL_VOLUME, image=DEFAULT_PULL_IMAGE,
                    revision=None, include=None, exclude=None,
                    token_env=DEFAULT_PULL_TOKEN_ENV):
    """Construct the ``docker run …`` argv that downloads ``repo_id`` into the
    NAMED docker ``volume`` by running ``hf download`` INSIDE the container.

    Correctness invariants (the whole point of this command):
      * Mounts the NAMED VOLUME at the HF cache (``-v <volume>:/root/.cache/huggingface``),
        NEVER a host ``C:/…`` bind mount — that's the 9P trap this command avoids (#15).
      * Overrides the image's default (model-server) ENTRYPOINT with ``hf`` so the
        container runs ``hf download <repo-id>``, not the vLLM server.
      * Uses the NEW ``hf`` CLI — never the removed ``huggingface-cli`` (deprecated
        and non-functional in huggingface_hub >=1.21).
      * ``token_env``: name of a host env var holding an HF token. By default this
        is ``HF_TOKEN``. When set, we add
        ``-e HF_TOKEN`` (BY NAME) so docker forwards the value from the child env —
        the token VALUE never appears on argv. Pass ``None`` only for an explicitly
        unauthenticated pull.
    """
    argv = ["docker", "run", "--rm",
            "-v", f"{volume}:{HF_CACHE_MOUNTPOINT}"]
    if token_env:
        # `-e HF_TOKEN` (no `=value`) tells docker to read HF_TOKEN from ITS OWN
        # environment — which is the child env we set in run_pull. The secret is
        # therefore passed by reference, never spliced onto the command line.
        argv += ["-e", "HF_TOKEN"]
    argv += ["--entrypoint", "hf", image, "download", repo_id]
    if revision:
        argv += ["--revision", revision]
    if include:
        argv += ["--include", include]
    if exclude:
        argv += ["--exclude", exclude]
    return argv


def _dotenv_value(path, name):
    """Return one variable from a simple dotenv file without logging its value."""
    expanded = os.path.expanduser(path)
    try:
        if not os.path.exists(expanded):
            return None
        if not os.path.isfile(expanded):
            raise ValueError(f"token file {expanded!r} is not a regular file")
        with open(expanded, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                key, value = line.split("=", 1)
                if key.strip() != name:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                else:
                    value = value.split(" #", 1)[0].rstrip()
                return value or None
    except FileNotFoundError:
        # The path can disappear between exists() and open(); treat it like a
        # missing optional source and fail closed if no environment token exists.
        return None
    except ValueError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ValueError(
            f"could not read token file {expanded!r}: {exc}"
        ) from exc
    return None


def run_pull(repo_id, volume=DEFAULT_PULL_VOLUME, image=DEFAULT_PULL_IMAGE,
             revision=None, include=None, exclude=None,
             token_env=DEFAULT_PULL_TOKEN_ENV,
             token_file=DEFAULT_PULL_TOKEN_FILE,
             dry_run=False, _run=subprocess.call, _environ=None):
    """Build and (unless ``dry_run``) execute the ``docker run … hf download`` argv.

    Streams the container's stdout/stderr live (``subprocess.call`` inherits the
    parent's fds). A non-zero docker exit is returned as a clean non-zero rc; a
    missing ``docker`` binary is reported and returned as rc 127 — never a traceback.

    ``hf download`` is resumable/idempotent (it skips complete files), so a repeat
    or a resumed pull is safe. Note (gotcha #12): a concurrent/interrupted download
    to the same cache can deadlock on ``.cache/huggingface/.gitignore.lock`` — if you
    see "Still waiting to acquire lock", stop the other download and resume ONE; do
    NOT delete the lock file blindly.
    """
    if any(sep in volume for sep in ("/", "\\", ":")):
        print(f"[anvil-serving] --volume {volume!r} looks like a PATH, not a docker "
              f"volume NAME; a host bind mount would reintroduce the 9P tax this "
              f"command exists to avoid (gotcha #15). Pass a named docker volume.",
              file=sys.stderr)
        return 2
    if not image or image.startswith("-"):
        print(
            "[anvil-serving] --image must be a Docker image reference, not an option",
            file=sys.stderr,
        )
        return 2
    option_like = [
        ("repository", repo_id),
        ("revision", revision),
        ("include", include),
        ("exclude", exclude),
    ]
    for label, value in option_like:
        if not value and label == "repository":
            print("[anvil-serving] repository id must not be empty", file=sys.stderr)
            return 2
        if isinstance(value, str) and value.startswith("-"):
            print(
                "[anvil-serving] %s must be a value, not a command option" % label,
                file=sys.stderr,
            )
            return 2
    environ = os.environ if _environ is None else _environ
    argv = build_pull_argv(repo_id, volume=volume, image=image, revision=revision,
                           include=include, exclude=exclude, token_env=token_env)

    child_env = dict(environ)
    if token_env and not dry_run:
        token = environ.get(token_env)
        if isinstance(token, str):
            token = token.strip()
        if not token and token_file:
            try:
                token = _dotenv_value(token_file, token_env)
            except ValueError as exc:
                print(f"[anvil-serving] {exc}", file=sys.stderr)
                return 2
        if not token:
            expanded = os.path.expanduser(token_file) if token_file else None
            source = f" or add it to {expanded!r}" if expanded else ""
            print(f"[anvil-serving] token variable {token_env!r} is empty/unset; "
                  f"export it{source}, or pass --no-token for an explicitly "
                  f"unauthenticated pull.", file=sys.stderr)
            return 2
        # Map the named var onto HF_TOKEN in the CHILD env only; `-e HF_TOKEN`
        # (added by build_pull_argv) forwards it into the container by reference.
        child_env["HF_TOKEN"] = token

    printable = " ".join(shlex.quote(t) for t in argv)
    if dry_run:
        # Preview does not need to resolve/read a secret: argv contains only the
        # variable name (`-e HF_TOKEN`), never its value.
        print("MODEL ARTIFACT PULL PLAN")
        print("repository: %s" % repo_id)
        print("revision: %s" % (revision or "(default branch)"))
        print("volume: %s" % volume)
        print("image: %s" % image)
        print("include: %s" % (include or "(all files)"))
        print("exclude: %s" % (exclude or "(none)"))
        if token_env:
            print("token environment variable: %s" % token_env)
            print(
                "token dotenv fallback: %s"
                % (
                    os.path.abspath(os.path.expanduser(token_file))
                    if token_file
                    else "(disabled)"
                )
            )
        else:
            print("token source: disabled (--no-token)")
        print("preconditions: Docker installed and running; named volume is writable")
        print("ordered actions: resolve token source; start one download container; run hf download")
        print("docker command:")
        print(printable)
        print("deferred until apply: confirmation, token read, image resolution, and Docker execution")
        print("recovery: rerun the same command; hf download resumes completed and partial files")
        print("rollback: none automatic; downloaded volume bytes remain until explicitly removed")
        return 0

    print(f"[anvil-serving] pulling {repo_id!r} into docker volume "
          f"{volume!r} (native ext4; avoids the 9P bind-mount tax)")
    print(f"[anvil-serving] $ {printable}", file=sys.stderr)
    try:
        return _run(argv, env=child_env)
    except OSError as exc:
        print(f"[anvil-serving] could not run `docker` ({exc}) - is Docker installed, "
              "on PATH, and running?", file=sys.stderr)
        return 127


def build_sync_argv(out=None, hf_roots="", model_dirs=""):
    """Construct the argv for ``anvil-serving models sync``.

    This is shared by the CLI-adjacent MCP preview path so agents can show the
    exact command before running a cache scan. It never resolves or inlines any
    credentials; the sync script reads ``HF_TOKEN`` from the process env if the
    operator has configured it. Callers add exactly one of ``--dry-run`` or
    ``--confirm`` so previews cannot be mistaken for authorized apply commands.
    """

    out = out or os.path.join(os.getcwd(), DEFAULT_CATALOG_DIR)
    argv = [sys.executable, "-m", "anvil_serving.cli", "models", "sync", "--out", out]
    if hf_roots:
        argv += ["--hf-roots", hf_roots]
    if model_dirs:
        argv += ["--model-dirs", model_dirs]
    return argv


def load_model_catalog(catalog_dir=None):
    """Read a generated model catalog from ``cards/*.json`` summaries.

    ``INDEX.md`` is intentionally ignored: it is a human table, while the JSON
    summaries are the structured contract written by ``models sync``.
    """

    catalog_dir = catalog_dir or os.path.join(os.getcwd(), DEFAULT_CATALOG_DIR)
    catalog_dir = os.path.abspath(catalog_dir)
    cards_dir = os.path.join(catalog_dir, "cards")
    index_path = os.path.join(catalog_dir, "INDEX.md")
    if not os.path.isdir(cards_dir):
        raise CatalogNotFound(catalog_dir)

    entries = []
    errors = []
    for name in sorted(os.listdir(cards_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(cards_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append({"path": path, "error": str(exc)})
            continue
        if not isinstance(data, dict):
            errors.append({"path": path, "error": "summary JSON must be an object"})
            continue
        if not isinstance(data.get("id"), str) or not data["id"].strip() or "format" not in data:
            errors.append({"path": path, "error": "summary must contain a non-empty id and format"})
            continue
        if not is_real_catalog_entry(data):
            continue
        entry = dict(data)
        entry["summary_path"] = path
        entries.append(entry)

    if errors:
        raise CatalogError("could not read one or more model catalog summaries", {
            "catalog_dir": catalog_dir,
            "errors": errors,
        })
    if not entries:
        raise CatalogNotFound(catalog_dir)

    entries.sort(key=lambda item: str(item.get("id") or item.get("repo") or item.get("summary_path") or ""))
    return {
        "catalog_dir": catalog_dir,
        "cards_dir": cards_dir,
        "index_path": index_path if os.path.exists(index_path) else None,
        "count": len(entries),
        "entries": entries,
    }


def pull_main(argv):
    ap = argparse.ArgumentParser(
        prog="anvil-serving models pull",
        description="Download a Hugging Face repo INTO A NAMED DOCKER VOLUME so it "
                    "is ready to serve natively. On Windows+WSL2+Docker, serving "
                    "weights from a C:/ bind mount reads over 9P (~15 MB/s, "
                    "18-90 min loads); a named docker volume is ext4-native inside "
                    "WSL2 (no 9P) and loads in seconds (CLAUDE.md gotcha #15). The "
                    "download runs `hf download` INSIDE a container with the volume "
                    "mounted at the HF cache, so bytes land on native ext4.\n\n"
                    "Examples:\n"
                    "  anvil-serving models pull openai/gpt-oss-120b --dry-run\n"
                    "  anvil-serving models pull openai/gpt-oss-120b --confirm",
        epilog="`hf download` is resumable/idempotent (it skips complete files). "
               "gotcha #12: a concurrent/interrupted download to the same cache "
               "can deadlock on .cache/huggingface/.gitignore.lock ('Still waiting "
               "to acquire lock'); stop the other download and resume ONE — do NOT "
               "delete the lock file blindly.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("repo_id", help="Hugging Face repo id, e.g. openai/gpt-oss-120b")
    ap.add_argument("--volume", default=DEFAULT_PULL_VOLUME,
                    help="named docker volume to pull into (default: %(default)s; "
                         "ext4-native inside WSL2 — avoids the 9P bind-mount tax)")
    ap.add_argument("--image", default=DEFAULT_PULL_IMAGE,
                    help="container image that ships the `hf` CLI; the download runs "
                         "inside it (default: %(default)s)")
    ap.add_argument("--revision", default=None,
                    help="git revision/branch/tag to download (passed to `hf download`)")
    ap.add_argument("--include", default=None,
                    help="glob of files to include (passed to `hf download`)")
    ap.add_argument("--exclude", default=None,
                    help="glob of files to exclude (passed to `hf download`)")
    ap.add_argument("--token-env", default=DEFAULT_PULL_TOKEN_ENV, metavar="ENV",
                    help="name of an env var holding an HF token; its value is "
                         "forwarded into the container as HF_TOKEN by reference "
                         "(never inlined on the command line). If the variable is "
                         "not exported, it is read from --token-file.")
    ap.add_argument("--token-file", default=DEFAULT_PULL_TOKEN_FILE, metavar="PATH",
                    help="dotenv file used when --token-env is not already exported "
                         "(default: %(default)s)")
    ap.add_argument("--no-token", action="store_true",
                    help="pull explicitly without forwarding HF_TOKEN")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the docker command that WOULD run, then exit")
    a = ap.parse_args(argv)
    return run_pull(a.repo_id, volume=a.volume, image=a.image, revision=a.revision,
                    include=a.include, exclude=a.exclude,
                    token_env=None if a.no_token else a.token_env,
                    token_file=a.token_file, dry_run=a.dry_run)


# The serve-recipe registry ships at <repo>/configs/serve-recipes.toml.
DEFAULT_REGISTRY = os.path.join("configs", "serve-recipes.toml")


def _default_registry():
    """Resolve the read registry using the documented configuration precedence."""
    project_registry = os.path.abspath(DEFAULT_REGISTRY)
    home_registry = paths.config_path("serve-recipes.toml")
    for candidate in (project_registry, home_registry):
        if os.path.isfile(candidate):
            return candidate
    packaged = resources.files("anvil_serving._scaffold_templates").joinpath(
        "serve-recipes.toml"
    )
    return os.fspath(packaged)


def _fmt_throughput(measured):
    m = measured or {}
    tps = m.get("throughput_single_tok_s")
    if isinstance(tps, (int, float)):
        return "%.1f tok/s" % tps
    agg = m.get("throughput_aggregate_tok_s")  # recorded at concurrency>1 (Copilot review)
    if isinstance(agg, (int, float)):
        conc = m.get("concurrency")
        return ("%.1f tok/s (agg x%s)" % (agg, conc)) if conc else ("%.1f tok/s (agg)" % agg)
    return "-"


def _fmt_intent(intent):
    intent = intent or {}
    if intent.get("mode"):
        return intent["mode"]
    suited = intent.get("suited") or []
    return ", ".join(suited) if suited else "-"


def _fmt_activation_roles(recipe):
    activation = recipe.get("activation") or {}
    if not isinstance(activation, dict):
        return "-"
    return ", ".join(sorted(str(role) for role in activation)) or "-"


def _print_recipe_table(registry):
    recipes = registry.get("recipe") or []
    headers = ["status", "model", "activates", "throughput", "intent"]
    rows = [[r.get("status", ""), r.get("model", ""),
             _fmt_activation_roles(r), _fmt_throughput(r.get("measured")),
             _fmt_intent(r.get("intent"))]
            for r in recipes]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))


def _print_recipe_show(recipe):
    print("model:  " + str(recipe.get("model", "")))
    print("status: " + str(recipe.get("status", "")))
    if recipe.get("source"):
        print("source: " + str(recipe["source"]))
    print()
    print("reproducible docker run:")
    print("  " + serve_recipes.reconstruct_docker_run(recipe))
    measured = recipe.get("measured") or {}
    if measured:
        print()
        print("measured:")
        for k, v in measured.items():
            print("  %s = %s" % (k, v))
    intent = recipe.get("intent") or {}
    if intent:
        print()
        print("intent:")
        for k in ("mode", "suited", "not_suited", "rationale"):
            if k in intent:
                v = intent[k]
                if isinstance(v, list):
                    v = ", ".join(str(x) for x in v)
                print("  %s: %s" % (k, v))
    activation = recipe.get("activation") or {}
    if isinstance(activation, dict) and activation:
        print()
        print("activation:")
        for role, details in sorted(activation.items()):
            print("  %s:" % role)
            if isinstance(details, dict):
                for key, value in details.items():
                    print("    %s: %s" % (key, value))
            else:
                print("    %s" % details)
            print(
                "    switch preview: anvil-serving serves switch %s %s --dry-run"
                % (role, recipe.get("model", ""))
            )
    download = recipe.get("download") or {}
    if download.get("command"):
        print()
        print("download:")
        print("  " + str(download["command"]))


def _recipe_registry(path, *, require_existing=True):
    if require_existing and not os.path.exists(path):
        raise serve_recipes.RecipeError("serve-recipe registry not found: %s" % path)
    if not os.path.exists(path):
        return {"schema": serve_recipes.REGISTRY_SCHEMA, "recipe": []}
    try:
        return serve_recipes.load_registry(path)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise serve_recipes.RecipeError(
            "cannot read serve-recipe registry %s: %s" % (path, exc)
        ) from exc


def _write_recipe_registry(path, registry, *, expected_digest):
    """Lock, reject state drift, back up, then atomically rewrite a registry."""
    # Render before backup so malformed operator input fails without creating
    # recovery artifacts for a mutation that never began.
    serve_recipes.format_registry(registry)
    with serve_recipes.registry_lock(path):
        if serve_recipes.registry_digest(path) != expected_digest:
            raise serve_recipes.RecipeError(
                "serve-recipe registry changed after it was read; retry the command"
            )
        backup = guard.backup_file(path)
        serve_recipes.write_registry(path, registry)
        return backup


def _confirm_recipe_mutation(action, model, *, confirm, dry_run):
    if dry_run:
        return True
    if guard.confirm(
        "Apply recipe %s for %r?" % (action, model),
        force=confirm,
    ):
        return True
    print("recipe mutation cancelled", file=sys.stderr)
    return False


def _help_description(outcome, *examples):
    return "%s\n\nExamples:\n%s" % (
        outcome,
        "\n".join("  " + example for example in examples),
    )


def _print_recipe_mutation_plan(
    action,
    registry_path,
    registry_digest,
    recipe,
    *,
    recipe_file=None,
    previous=None,
):
    print("RECIPE %s PLAN" % action.upper())
    print("registry: %s" % os.path.abspath(registry_path))
    print("registry digest: %s" % (registry_digest or "(new registry)"))
    if recipe_file:
        print("recipe input: %s" % os.path.abspath(recipe_file))
        print(
            "recipe input digest: %s"
            % (serve_recipes.registry_digest(recipe_file) or "(unreadable)")
        )
    if previous is not None:
        print("current model: %s" % previous.get("model", ""))
    entry_label = "recipe selected for deletion" if action == "delete" else "proposed registry entry"
    print(entry_label + ":")
    for line in serve_recipes.format_recipe(recipe).rstrip().splitlines():
        print("  " + line)
    if registry_digest:
        print("ordered actions: lock registry; verify digest; create backup; atomically write")
        print("manual recovery: restore the numbered .anvil.bak.N registry backup")
    else:
        print("ordered actions: lock registry; verify it is still absent; atomically create")
        print("manual recovery: remove the newly created registry")
    print("deferred until apply: confirmation and registry drift recheck")


def _build_recipe_parser():
    ap = argparse.ArgumentParser(
        prog="anvil-serving models recipes",
        description="Discover, edit, and load reusable model serve recipes.",
    )
    sub = ap.add_subparsers(dest="recipe_action", required=True)
    p_list = sub.add_parser(
        "list",
        help="table the recorded serve recipes",
        description=_help_description(
            "List the recipes in one registry with status, throughput, and intent.",
            "anvil-serving models recipes list",
            "anvil-serving models recipes list --registry REGISTRY",
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_list.add_argument("--registry", default=None,
                        help="registry TOML (default precedence: project, config home, packaged)")
    p_show = sub.add_parser(
        "show",
        help="reproducible docker run + measured stats for a model",
        description=_help_description(
            "Show one resolved recipe, including its reproducible Docker command and evidence.",
            "anvil-serving models recipes show gpt-oss-120b",
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_show.add_argument("model", metavar="MODEL",
                        help="model id (exact or basename, e.g. gpt-oss-120b)")
    p_show.add_argument("--registry", default=None,
                        help="registry TOML (default precedence: project, config home, packaged)")
    for action, summary in (
        ("create", "add exactly one recipe from a TOML file"),
        ("update", "replace a selected recipe from a TOML file"),
    ):
        example = (
            "anvil-serving models recipes create --recipe-file ./candidate.toml "
            "--registry ./serve-recipes.local.toml"
            if action == "create"
            else "anvil-serving models recipes update MODEL --recipe-file ./candidate.toml "
                 "--registry ./serve-recipes.local.toml"
        )
        parser = sub.add_parser(
            action,
            help=summary,
            description=_help_description(
                (
                    "Create one recipe in an operator-owned registry."
                    if action == "create"
                    else "Replace one selected recipe in an operator-owned registry."
                ),
                example + " --dry-run",
                example + " --confirm",
            ),
            epilog="Apply writes atomically and preserves a numbered backup when a prior "
                   "registry exists.",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        if action == "update":
            parser.add_argument("model", metavar="MODEL",
                                help="current model id or unambiguous basename")
        parser.add_argument("--recipe-file", required=True, metavar="PATH",
                            help="TOML containing exactly one [[recipe]] table")
        parser.add_argument("--registry", required=True, metavar="TOML",
                            help="operator-owned registry TOML to mutate")
        parser.add_argument("--dry-run", action="store_true",
                            help="validate and preview without writing")
        parser.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)
    p_delete = sub.add_parser(
        "delete",
        help="remove one recipe from an operator registry",
        description=_help_description(
            "Delete one selected recipe from an operator-owned registry.",
            "anvil-serving models recipes delete MODEL --registry REGISTRY --dry-run",
            "anvil-serving models recipes delete MODEL --registry REGISTRY --confirm",
        ),
        epilog="Apply writes atomically and preserves a numbered backup.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_delete.add_argument("model", metavar="MODEL",
                          help="model id or unambiguous basename")
    p_delete.add_argument("--registry", required=True, metavar="TOML",
                          help="operator-owned registry TOML to mutate")
    p_delete.add_argument("--dry-run", action="store_true",
                          help="preview without writing")
    p_delete.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)
    p_load = sub.add_parser(
        "load",
        help="start a named local container from one recipe",
        description=_help_description(
            "Start a new loopback-bound Docker container from one recorded recipe.",
            "anvil-serving models recipes load MODEL --container NAME --dry-run",
            "anvil-serving models recipes load MODEL --container NAME --confirm",
        ),
        epilog="Loading does not change router policy. Run eval preflight next; use serves "
               "switch only after human review.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_load.add_argument("model", metavar="MODEL",
                        help="model id or unambiguous basename")
    p_load.add_argument("--container", required=True, metavar="NAME",
                        help="new Docker container name for this loaded recipe")
    p_load.add_argument("--registry", default=None,
                        help="registry TOML (default precedence: project, config home, packaged)")
    p_load.add_argument("--dry-run", action="store_true",
                        help="print the exact docker command without starting it")
    p_load.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)
    return ap


def _recipe_main(argv):
    a = _build_recipe_parser().parse_args(argv)

    registry_path = a.registry or _default_registry()
    try:
        registry = _recipe_registry(
            registry_path,
            require_existing=a.recipe_action != "create",
        )
        registry_digest = (
            serve_recipes.registry_digest(registry_path)
            if a.recipe_action in {"create", "update", "delete"}
            else None
        )
    except serve_recipes.RecipeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if a.recipe_action == "list":
        _print_recipe_table(registry)
        return 0
    if a.recipe_action == "show":
        recipe = serve_recipes.find_recipe(registry, a.model)
        if recipe is None:
            print("no serve recipe for %r in %s" % (a.model, registry_path), file=sys.stderr)
            return 1
        _print_recipe_show(recipe)
        return 0
    if a.recipe_action in {"create", "update"}:
        try:
            replacement = serve_recipes.load_recipe_file(a.recipe_file)
            if a.recipe_action == "create":
                updated = serve_recipes.create_recipe(registry, replacement)
                model = replacement["model"]
                previous = None
            else:
                updated, previous = serve_recipes.update_recipe(registry, a.model, replacement)
                model = replacement["model"]
        except serve_recipes.RecipeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if a.dry_run:
            _print_recipe_mutation_plan(
                a.recipe_action,
                registry_path,
                registry_digest,
                replacement,
                recipe_file=a.recipe_file,
                previous=previous,
            )
            return 0
        if not _confirm_recipe_mutation(a.recipe_action, model, confirm=a.confirm, dry_run=False):
            return 3
        try:
            backup = _write_recipe_registry(
                registry_path, updated, expected_digest=registry_digest
            )
        except (OSError, serve_recipes.RecipeError) as exc:
            print("cannot write serve-recipe registry %s: %s" % (registry_path, exc), file=sys.stderr)
            return 1
        print("%sd recipe %r in %s" % ("create" if a.recipe_action == "create" else "update", model, registry_path))
        if backup:
            print("backup: %s" % backup)
        return 0
    if a.recipe_action == "delete":
        try:
            updated, deleted = serve_recipes.delete_recipe(registry, a.model)
        except serve_recipes.RecipeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if a.dry_run:
            _print_recipe_mutation_plan(
                "delete",
                registry_path,
                registry_digest,
                deleted,
                previous=deleted,
            )
            return 0
        if not _confirm_recipe_mutation("delete", deleted["model"], confirm=a.confirm, dry_run=False):
            return 3
        try:
            backup = _write_recipe_registry(
                registry_path, updated, expected_digest=registry_digest
            )
        except (OSError, serve_recipes.RecipeError) as exc:
            print("cannot write serve-recipe registry %s: %s" % (registry_path, exc), file=sys.stderr)
            return 1
        print("deleted recipe %r from %s" % (deleted["model"], registry_path))
        if backup:
            print("backup: %s" % backup)
        return 0
    if a.recipe_action == "load":
        try:
            recipe = serve_recipes.find_recipe(registry, a.model)
            if recipe is None:
                raise serve_recipes.RecipeError("no serve recipe for %r in %s" % (a.model, registry_path))
            command = serve_recipes.docker_run_argv(recipe, container=a.container)
        except serve_recipes.RecipeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        printable = shlex.join(command)
        if a.dry_run:
            print("RECIPE LOAD PLAN")
            print("registry: %s" % os.path.abspath(registry_path))
            print("registry digest: %s" % serve_recipes.registry_digest(registry_path))
            print("model: %s" % recipe["model"])
            print("container: %s" % a.container)
            print("docker command:")
            print(printable)
            print(
                "recovery after a successful start by this command: docker rm -f %s"
                % shlex.quote(a.container)
            )
            print("ownership condition: never remove a container that existed before apply")
            print("deferred until apply: confirmation and Docker start")
            print("not performed by load: health check and eval preflight; run them next")
            return 0
        if not _confirm_recipe_mutation("load", recipe["model"], confirm=a.confirm, dry_run=False):
            return 3
        print("loading recipe %r as container %r" % (recipe["model"], a.container))
        print("$ " + printable)
        _command, rc = serve_recipes.load_recipe(recipe, a.container)
        if rc:
            print("recipe load failed with docker exit code %s" % rc, file=sys.stderr)
            return rc
        port = recipe.get("serve", {}).get("port")
        if port:
            print("next: preflight before trusting this serve: anvil-serving eval preflight --base-url http://127.0.0.1:%s/v1 --model %s --confirm" % (port, recipe["model"]))
        return 0
    return 2


def _resolved_model_dirs(explicit="", *, environ=None):
    """Return the plain-model directories the sync subprocess will inspect."""
    environ = os.environ if environ is None else environ
    configured = explicit or environ.get("ANVIL_MODEL_DIRS", "")
    candidates = [item for item in configured.split(os.pathsep) if item]
    candidates.extend(glob.glob("/mnt/c/Users/*/models"))
    resolved = []
    seen = set()
    for candidate in candidates:
        normalized = os.path.normpath(os.path.abspath(os.path.expanduser(candidate)))
        if normalized in seen or not os.path.isdir(normalized):
            continue
        seen.add(normalized)
        resolved.append(normalized)
    return resolved


def _next_catalog_backup(path):
    index = 1
    while True:
        candidate = "%s.anvil.bak.%s" % (path, index)
        if not os.path.exists(candidate):
            return candidate
        index += 1


class CatalogSyncBusy(RuntimeError):
    """Another process already owns the catalog replacement lock."""


@contextmanager
def _catalog_lock(path):
    """Hold one non-blocking cross-platform lock for a catalog sync."""
    parent = os.path.dirname(path)
    basename = os.path.basename(path) or "catalog"
    lock_path = os.path.join(parent, ".%s.anvil-sync.lock" % basename)
    handle = open(lock_path, "a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise CatalogSyncBusy from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise CatalogSyncBusy from exc
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _catalog_output_error(path):
    """Return a refusal reason unless ``path`` is a safe managed catalog target."""
    output = os.path.abspath(path)
    resolved = os.path.normcase(os.path.realpath(output))
    protected = {
        os.path.normcase(os.path.realpath(os.getcwd())): "the current working directory",
        os.path.normcase(os.path.realpath(os.path.expanduser("~"))): "the user home directory",
        os.path.normcase(os.path.realpath(os.path.dirname(HERE))): "the anvil-serving checkout",
    }
    if resolved in protected:
        return "refusing catalog output %s: it is %s" % (output, protected[resolved])
    if os.path.lexists(output) and (os.path.islink(output) or not os.path.isdir(output)):
        return "refusing catalog output %s: existing target must be a directory, not a file or link" % output
    if os.path.isdir(output):
        try:
            entries = os.listdir(output)
        except OSError as exc:
            return "cannot inspect catalog output %s: %s" % (output, exc)
        if entries and not (
            os.path.isdir(os.path.join(output, "cards"))
            and os.path.isfile(os.path.join(output, "INDEX.md"))
        ):
            return (
                "refusing catalog output %s: existing non-empty directory is not an "
                "anvil model catalog (expected cards/ and INDEX.md)" % output
            )
    return None


def _configured_hf_roots(explicit="", *, environ=None):
    environ = os.environ if environ is None else environ
    candidates = []
    for configured in (environ.get("ANVIL_HF_ROOTS", ""), explicit):
        candidates.extend(item for item in configured.split(os.pathsep) if item)
    return config.hf_cache_roots(candidates)


def _staged_catalog_error(path):
    """Return a reason when a successful child did not produce a valid catalog."""
    cards = os.path.join(path, "cards")
    index = os.path.join(path, "INDEX.md")
    if not os.path.isdir(cards) or not os.path.isfile(index):
        return "sync produced an incomplete catalog (expected cards/ and INDEX.md)"
    real_entries = 0
    for card_path in glob.glob(os.path.join(cards, "*.json")):
        try:
            with open(card_path, encoding="utf-8") as handle:
                card = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            return "sync produced an unreadable card %s: %s" % (card_path, exc)
        if not isinstance(card, dict):
            return "sync produced a non-object card %s" % card_path
        if not isinstance(card.get("id"), str) or not card["id"].strip() or "format" not in card:
            return "sync produced a card without a non-empty id and format: %s" % card_path
        if is_real_catalog_entry(card):
            real_entries += 1
    if real_entries == 0:
        return "sync produced no readable model entries; the active catalog was not replaced"
    return None


def _sync_apply(output, roots, model_dirs):
    """Build and atomically install one complete catalog while holding its lock."""
    parent = os.path.dirname(output)
    staging = None
    try:
        staging = tempfile.mkdtemp(
            prefix=".%s.anvil-sync-" % (os.path.basename(output) or "catalog"),
            dir=parent,
        )
        os.makedirs(os.path.join(staging, "cards"), exist_ok=True)
    except OSError as exc:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)
        print("cannot create staged model catalog beside %s: %s" % (output, exc), file=sys.stderr)
        return 1
    env = dict(os.environ, ANVIL_MODELS_OUT=staging, PYTHONUTF8="1")
    if roots:
        env["ANVIL_HF_ROOTS"] = os.pathsep.join(roots)
    else:
        env.pop("ANVIL_HF_ROOTS", None)
    if model_dirs:
        env["ANVIL_MODEL_DIRS"] = os.pathsep.join(model_dirs)
    else:
        env.pop("ANVIL_MODEL_DIRS", None)
    try:
        completed = subprocess.run(
            [sys.executable, os.path.join(HERE, "_sync.py")],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        print("cannot launch model catalog worker: %s" % exc, file=sys.stderr)
        return 4
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode:
        shutil.rmtree(staging, ignore_errors=True)
        return int(completed.returncode)
    staged_error = _staged_catalog_error(staging)
    if staged_error:
        print(staged_error, file=sys.stderr)
        shutil.rmtree(staging, ignore_errors=True)
        return 1
    backup = None
    try:
        if os.path.exists(output):
            backup = _next_catalog_backup(output)
            os.replace(output, backup)
        os.replace(staging, output)
        staging = None
    except OSError as exc:
        if backup and not os.path.exists(output) and os.path.exists(backup):
            try:
                os.replace(backup, output)
            except OSError:
                recovery_code = "import os; os.replace(%r, %r)" % (backup, output)
                recovery_argv = [sys.executable, "-c", recovery_code]
                recovery_command = (
                    subprocess.list2cmdline(recovery_argv)
                    if os.name == "nt"
                    else shlex.join(recovery_argv)
                )
                print(
                    "catalog replacement failed; prior catalog is preserved at %s" % backup,
                    file=sys.stderr,
                )
                print(
                    "recovery command: %s" % recovery_command,
                    file=sys.stderr,
                )
                return 5
        print("cannot replace model catalog %s: %s" % (output, exc), file=sys.stderr)
        return 1
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)
    if backup:
        print("backup: %s" % backup)
    return 0


def _sync_main(args):
    roots = _configured_hf_roots(args.hf_roots)
    model_dirs = _resolved_model_dirs(args.model_dirs)
    output = os.path.abspath(args.out)
    output_error = _catalog_output_error(output)
    if output_error:
        print(output_error, file=sys.stderr)
        return 3
    if args.dry_run:
        print("MODEL CATALOG SYNC PLAN")
        print("output: %s" % output)
        print("Hugging Face roots: %s" % (os.pathsep.join(roots) or "(auto-detected none)"))
        print("plain model directories: %s" % (os.pathsep.join(model_dirs) or "(none)"))
        print("actions: scan local model roots; stage cards/ summaries and INDEX.md; replace the catalog")
        print("rollback: preserve the prior catalog as a numbered .anvil.bak.N directory")
        print("deferred until apply: filesystem scan, model-card fetches, and catalog writes")
        return 0
    parent = os.path.dirname(output)
    try:
        os.makedirs(parent, exist_ok=True)
        with _catalog_lock(output):
            output_error = _catalog_output_error(output)
            if output_error:
                print(output_error, file=sys.stderr)
                return 3
            return _sync_apply(output, roots, model_dirs)
    except CatalogSyncBusy:
        print(
            "model catalog sync already in progress for %s; wait for it to finish" % output,
            file=sys.stderr,
        )
        return 4
    except OSError as exc:
        print("cannot prepare model catalog output %s: %s" % (output, exc), file=sys.stderr)
        return 1


def main(argv):
    argv = list(argv)
    ap = argparse.ArgumentParser(
        prog="anvil-serving models",
        description="Model catalog, Hugging Face volume pulls, and recorded serve recipes.",
    )
    sub = ap.add_subparsers(dest="action", required=True)

    sync = sub.add_parser(
        "sync",
        help="scan HF caches and build the model catalog",
        description=_help_description(
            "Scan configured local model roots and write a structured model catalog.",
            "anvil-serving models sync --out CATALOG --dry-run",
            "anvil-serving models sync --out CATALOG --confirm",
        ),
        epilog="Apply stages a complete replacement, preserves the prior catalog as "
               "a numbered backup, then installs cards/*.json and INDEX.md.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sync.add_argument("--out", default=os.path.join(os.getcwd(), DEFAULT_CATALOG_DIR),
                      help="output dir for cards/ + INDEX.md")
    sync.add_argument("--hf-roots", default="", help="extra HF cache roots (os.pathsep-separated)")
    sync.add_argument("--model-dirs", default="", help="extra plain model dirs (os.pathsep-separated)")
    sync.add_argument("--dry-run", action="store_true",
                      help="resolve sources and preview catalog writes without scanning or writing")

    sub.add_parser("pull", help="download a Hugging Face repo into a named Docker volume")
    sub.add_parser("recipe", help="create, inspect, edit, delete, or load serve recipes")
    sub.add_parser("cache", help="model cache inspection and cleanup helpers")
    sub.add_parser("score", help="rank models for roles from benchmark evidence")

    if argv and argv[0] == "pull":
        return pull_main(argv[1:])
    if argv and argv[0] == "recipe":
        return _recipe_main(argv[1:])
    if argv and argv[0] == "cache":
        if len(argv) > 1 and argv[1] == "prune":
            from . import cache_prune
            return cache_prune.main(
                argv[2:],
                prog="anvil-serving models cache prune",
            )
        cache_ap = argparse.ArgumentParser(prog="anvil-serving models cache")
        cache_sub = cache_ap.add_subparsers(dest="cache_action", required=True)
        cache_sub.add_parser("prune", help="plan and gate Hugging Face cache cleanup")
        cache_ap.parse_args(argv[1:])
        return 2
    if argv and argv[0] == "score":
        from . import score
        return score.main(argv[1:], prog="anvil-serving models score")

    args = ap.parse_args(argv)
    if args.action == "sync":
        return _sync_main(args)
    return 2
