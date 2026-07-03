"""`anvil-serving models` — catalog + fetch verbs for local model management.

Two sub-actions:
  * ``sync`` — scan HF caches + model dirs, pull cards, build the catalog (-> `_sync.py`).
  * ``pull`` — download a Hugging Face repo INTO A NAMED DOCKER VOLUME so it's ready
    to serve natively (see ``pull_main`` / ``build_pull_argv`` below).

Why ``pull`` mounts a NAMED VOLUME and not a host ``C:/…`` path (CLAUDE.md gotcha #15):
on this Windows + WSL2 + Docker box, serving weights from a ``C:/…`` bind mount reads
over 9P (~15 MB/s → 18–90 min cold loads); a named docker volume is ext4-native inside
the WSL2 VM (no 9P) and loads in seconds. So we download the repo straight into the
volume, then serve later with the repo-id as ``--model`` — bytes never touch 9P.
"""
import os
import argparse
import shlex
import subprocess
import sys
from . import config
HERE = os.path.dirname(__file__)

# `pull` defaults. The vLLM nightly image ships the `hf` CLI (huggingface_hub), so
# the download runs INSIDE it with the named volume mounted at the HF cache.
DEFAULT_PULL_VOLUME = "vllm-hfcache"
DEFAULT_PULL_IMAGE = "vllm/vllm-openai:nightly"
# Where the HF cache lives inside the container — the volume is mounted here so
# downloaded blobs land on native ext4, not a 9P bind mount (gotcha #15).
HF_CACHE_MOUNTPOINT = "/root/.cache/huggingface"


def build_pull_argv(repo_id, volume=DEFAULT_PULL_VOLUME, image=DEFAULT_PULL_IMAGE,
                    revision=None, include=None, exclude=None, token_env=None):
    """Construct the ``docker run …`` argv that downloads ``repo_id`` into the
    NAMED docker ``volume`` by running ``hf download`` INSIDE the container.

    Correctness invariants (the whole point of this command):
      * Mounts the NAMED VOLUME at the HF cache (``-v <volume>:/root/.cache/huggingface``),
        NEVER a host ``C:/…`` bind mount — that's the 9P trap this command avoids (#15).
      * Overrides the image's default (model-server) ENTRYPOINT with ``hf`` so the
        container runs ``hf download <repo-id>``, not the vLLM server.
      * Uses the NEW ``hf`` CLI — never the removed ``huggingface-cli`` (deprecated
        and non-functional in huggingface_hub >=1.21).
      * ``token_env``: name of a host env var holding an HF token. When set, we add
        ``-e HF_TOKEN`` (BY NAME) so docker forwards the value from the child env —
        the token VALUE never appears on argv. Default: UNauthenticated (unauthenticated
        HF works on this box; there is no HF_TOKEN in the .env — gotcha #12/CLAUDE.md).
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


def run_pull(repo_id, volume=DEFAULT_PULL_VOLUME, image=DEFAULT_PULL_IMAGE,
             revision=None, include=None, exclude=None, token_env=None,
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
    environ = os.environ if _environ is None else _environ
    argv = build_pull_argv(repo_id, volume=volume, image=image, revision=revision,
                           include=include, exclude=exclude, token_env=token_env)

    child_env = dict(environ)
    if token_env:
        token = environ.get(token_env)
        if not token:
            print(f"[anvil-serving] --token-env {token_env!r} is set but that "
                  f"environment variable is empty/unset; export it or drop "
                  f"--token-env to pull unauthenticated.", file=sys.stderr)
            return 2
        # Map the named var onto HF_TOKEN in the CHILD env only; `-e HF_TOKEN`
        # (added by build_pull_argv) forwards it into the container by reference.
        child_env["HF_TOKEN"] = token

    printable = " ".join(shlex.quote(t) for t in argv)
    if dry_run:
        # Safe to print: the token is passed via env, so it never appears here.
        print(printable)
        return 0

    print(f"[anvil-serving] pulling {repo_id!r} into docker volume "
          f"{volume!r} (native ext4; avoids the 9P bind-mount tax)")
    print(f"[anvil-serving] $ {printable}", file=sys.stderr)
    try:
        return _run(argv, env=child_env)
    except FileNotFoundError:
        print("[anvil-serving] `docker` not found on PATH — is Docker installed "
              "and running?", file=sys.stderr)
        return 127


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
    ap.add_argument("--token-env", default=None, metavar="ENV",
                    help="name of an env var holding an HF token; its value is "
                         "forwarded into the container as HF_TOKEN by reference "
                         "(never inlined on the command line). Default: "
                         "UNauthenticated (unauthenticated HF works on this box).")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the docker command that WOULD run, then exit")
    a = ap.parse_args(argv)
    return run_pull(a.repo_id, volume=a.volume, image=a.image, revision=a.revision,
                    include=a.include, exclude=a.exclude, token_env=a.token_env,
                    dry_run=a.dry_run)


def main(argv):
    argv = list(argv)
    # `pull` has a wholly different arg surface than `sync`; branch it out before
    # the `sync` argparser (which owns the `action` positional) ever sees it.
    if argv and argv[0] == "pull":
        return pull_main(argv[1:])

    ap = argparse.ArgumentParser(prog="anvil-serving models")
    ap.add_argument("action", choices=["sync", "pull"],
                    help="sync = refresh the catalog; "
                         "pull = download a HF repo into a named docker volume")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "model-library"),
                    help="output dir for cards/ + INDEX.md")
    ap.add_argument("--hf-roots", default="", help="extra HF cache roots (os.pathsep-separated)")
    ap.add_argument("--model-dirs", default="", help="extra plain model dirs (os.pathsep-separated)")
    a = ap.parse_args(argv)
    # Only "sync" reaches here — "pull" was dispatched above.
    os.makedirs(os.path.join(a.out, "cards"), exist_ok=True)
    roots = os.pathsep.join(config.hf_cache_roots(a.hf_roots.split(os.pathsep) if a.hf_roots else None))
    env = dict(os.environ, ANVIL_MODELS_OUT=a.out)
    if roots: env["ANVIL_HF_ROOTS"] = roots
    if a.model_dirs: env["ANVIL_MODEL_DIRS"] = a.model_dirs
    return subprocess.call([sys.executable, os.path.join(HERE, "_sync.py")], env=env)
