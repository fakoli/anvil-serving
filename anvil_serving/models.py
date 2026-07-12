"""`anvil-serving models` — catalog + fetch verbs for local model management.

Two sub-actions:
  * ``sync`` — scan HF caches + model dirs, pull cards, build the catalog (-> `_sync.py`).
  * ``pull`` — download a Hugging Face repo INTO A NAMED DOCKER VOLUME so it's ready
    to serve natively (see ``pull_main`` / ``build_pull_argv`` below).
  * ``recipe`` — READ recorded serve recipes (``recipe list`` / ``recipe show <model>``);
    the GENERATE half is ``benchmark --recipe-out``.

Why ``pull`` mounts a NAMED VOLUME and not a host ``C:/…`` path (CLAUDE.md gotcha #15):
on this Windows + WSL2 + Docker box, serving weights from a ``C:/…`` bind mount reads
over 9P (~15 MB/s → 18–90 min cold loads); a named docker volume is ext4-native inside
the WSL2 VM (no 9P) and loads in seconds. So we download the repo straight into the
volume, then serve later with the repo-id as ``--model`` — bytes never touch 9P.
"""
import os
import argparse
import json
import shlex
import subprocess
import tomllib
import sys
from . import config
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
        print(printable)
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
    operator has configured it.
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
                    "mounted at the HF cache, so bytes land on native ext4.",
        epilog="`hf download` is resumable/idempotent (it skips complete files). "
               "gotcha #12: a concurrent/interrupted download to the same cache "
               "can deadlock on .cache/huggingface/.gitignore.lock ('Still waiting "
               "to acquire lock'); stop the other download and resume ONE — do NOT "
               "delete the lock file blindly.",
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
    """Resolve the default registry path regardless of cwd (cwd-relative first, then
    repo-root-relative so `python -m anvil_serving.cli models recipes ...` works anywhere)."""
    if os.path.exists(DEFAULT_REGISTRY):
        return DEFAULT_REGISTRY
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(repo_root, "configs", "serve-recipes.toml")
    return candidate if os.path.exists(candidate) else DEFAULT_REGISTRY


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


def _print_recipe_table(registry):
    recipes = registry.get("recipe") or []
    headers = ["status", "model", "throughput", "intent"]
    rows = [[r.get("status", ""), r.get("model", ""),
             _fmt_throughput(r.get("measured")), _fmt_intent(r.get("intent"))]
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
    download = recipe.get("download") or {}
    if download.get("command"):
        print()
        print("download:")
        print("  " + str(download["command"]))


def _recipe_main(argv):
    ap = argparse.ArgumentParser(prog="anvil-serving models recipes")
    sub = ap.add_subparsers(dest="recipe_action", required=True)
    p_list = sub.add_parser("list", help="table the recorded serve recipes")
    p_list.add_argument("--registry", default=None, help="registry TOML (default: %s)" % DEFAULT_REGISTRY)
    p_show = sub.add_parser("show", help="reproducible docker run + measured stats for a model")
    p_show.add_argument("model", help="model id (exact or basename, e.g. gpt-oss-120b)")
    p_show.add_argument("--registry", default=None, help="registry TOML (default: %s)" % DEFAULT_REGISTRY)
    a = ap.parse_args(argv)

    registry_path = a.registry or _default_registry()
    if not os.path.exists(registry_path):
        print("serve-recipe registry not found: %s" % registry_path, file=sys.stderr)
        return 1
    try:
        registry = serve_recipes.load_registry(registry_path)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        print("cannot read serve-recipe registry %s: %s" % (registry_path, exc), file=sys.stderr)
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
    return 2


def _sync_main(args):
    os.makedirs(os.path.join(args.out, "cards"), exist_ok=True)
    roots = os.pathsep.join(config.hf_cache_roots(args.hf_roots.split(os.pathsep) if args.hf_roots else None))
    env = dict(os.environ, ANVIL_MODELS_OUT=args.out)
    if roots:
        env["ANVIL_HF_ROOTS"] = roots
    if args.model_dirs:
        env["ANVIL_MODEL_DIRS"] = args.model_dirs
    return subprocess.call([sys.executable, os.path.join(HERE, "_sync.py")], env=env)


def main(argv):
    argv = list(argv)
    ap = argparse.ArgumentParser(
        prog="anvil-serving models",
        description="Model catalog, Hugging Face volume pulls, and recorded serve recipes.",
    )
    sub = ap.add_subparsers(dest="action", required=True)

    sync = sub.add_parser("sync", help="scan HF caches and build the model catalog")
    sync.add_argument("--out", default=os.path.join(os.getcwd(), DEFAULT_CATALOG_DIR),
                      help="output dir for cards/ + INDEX.md")
    sync.add_argument("--hf-roots", default="", help="extra HF cache roots (os.pathsep-separated)")
    sync.add_argument("--model-dirs", default="", help="extra plain model dirs (os.pathsep-separated)")

    sub.add_parser("pull", help="download a Hugging Face repo into a named Docker volume")
    sub.add_parser("recipe", help="list or show recorded serve recipes")
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
