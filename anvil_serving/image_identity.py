"""Read-only, bounded build identity for one immutable local Docker image."""
from __future__ import annotations

import argparse
import json
import re
import subprocess

from .docker_images import DockerImageCleanupError, normalize_immutable_image_reference


DEFAULT_LABELS = (
    "org.opencontainers.image.source",
    "org.opencontainers.image.revision",
    "org.opencontainers.image.version",
)
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}\Z")


def inspect_image_identity(image, *, labels=(), runner=subprocess.run):
    """Inspect only one image, returning selected build labels and no environment."""
    reference = normalize_immutable_image_reference(image)
    selected = tuple(dict.fromkeys((*DEFAULT_LABELS, *labels)))
    if len(selected) > 64 or any(not isinstance(key, str) or not _LABEL.fullmatch(key)
                                 for key in selected):
        raise DockerImageCleanupError("request at most 64 valid image label names")
    # Filter in Docker itself: unrelated labels, environment, and history never
    # enter the command output retained by this tool.
    fields = ['"image_id":{{json .Id}}', '"repo_digests":{{json .RepoDigests}}',
              '"os":{{json .Os}}', '"architecture":{{json .Architecture}}']
    label_fields = [json.dumps(key) + ':{{json (index .Config.Labels ' + json.dumps(key) + ')}}'
                    for key in selected]
    template = '{' + ','.join(fields) + ',"labels":{' + ','.join(label_fields) + '}}'
    try:
        result = runner(["docker", "image", "inspect", "--format", template, reference],
                        capture_output=True, text=True, errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DockerImageCleanupError("image identity inspection unavailable") from exc
    if result.returncode:
        raise DockerImageCleanupError("Docker could not inspect the requested local image")
    if len(result.stdout) > 300_000:
        raise DockerImageCleanupError("image identity exceeds the evidence size limit")
    try:
        row = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise DockerImageCleanupError("Docker returned invalid image identity JSON") from exc
    if not isinstance(row, dict) or set(row) != {
        "image_id", "repo_digests", "os", "architecture", "labels"
    } or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(row.get("image_id"))):
        raise DockerImageCleanupError("Docker did not resolve one immutable image ID")
    digests = row.get("repo_digests") or []
    if not isinstance(digests, list) or len(digests) > 256 or any(
        not isinstance(item, str) or len(item) > 4096 for item in digests
    ):
        raise DockerImageCleanupError("Docker returned invalid repository digests")
    if reference.startswith("sha256:"):
        if row["image_id"] != reference:
            raise DockerImageCleanupError("resolved image ID does not match requested identity")
    elif reference not in digests:
        raise DockerImageCleanupError("resolved repository digest does not match requested identity")
    for field in ("os", "architecture"):
        if not isinstance(row.get(field), str) or not re.fullmatch(r"[a-z0-9_+-]{1,64}", row[field]):
            raise DockerImageCleanupError("Docker returned invalid platform identity")
    values = row.get("labels")
    if not isinstance(values, dict) or set(values) != set(selected) or any(
        value is not None and (not isinstance(value, str) or len(value) > 4096)
        for value in values.values()
    ):
        raise DockerImageCleanupError("Docker returned invalid selected build labels")
    return {"schema": "docker-image-identity/v1", "requested_identity": reference,
            **row, "repo_digests": sorted(digests)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Full image ID or repository@sha256 digest already cached locally")
    parser.add_argument("--label", action="append", default=[], help="Additional build label to return; repeatable")
    args = parser.parse_args(argv)
    try:
        result = inspect_image_identity(args.image, labels=args.label)
    except DockerImageCleanupError as exc:
        print(json.dumps({"schema": "docker-image-identity/v1", "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
