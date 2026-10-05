"""Selected, cached SWE instance image identities for paired evaluations."""

from __future__ import annotations

import json
import re

from .jobs import BenchmarkJobError


_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def validate_paired_image_ids(value, selected):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != set(selected)
            or any(not isinstance(v, str) or not _IMAGE_ID.fullmatch(v)
                   for v in value.values())):
        raise BenchmarkJobError("bad_swe_image_policy", "paired image IDs must match every selected instance exactly")
    return {instance: value[instance] for instance in selected}


def instance_image_tag(instance):
    # Exact naming convention in the pinned x86_64 mini-agent and grader.
    return "swebench/sweb.eval.x86_64." + instance.replace("__", "_1776_").lower() + ":latest"


def observe_paired_images(expected, *, executable, runner, cwd, env):
    """Read selected cached tags only; missing or changed images never trigger pulls."""
    observations = []
    template = '{"image_id":{{json .Id}},"repo_digests":{{json .RepoDigests}}}'
    for instance, image_id in expected.items():
        tag = instance_image_tag(instance)
        try:
            result = runner([executable, "image", "inspect", "--format", template, tag], cwd, 30, env)
            if result.returncode or len(result.stdout) > 65536:
                raise ValueError("missing image or oversized observation")
            row = json.loads(result.stdout)
            digests = row.get("repo_digests") or []
            if (row.get("image_id") != image_id or not isinstance(digests, list)
                    or len(digests) > 256 or any(not isinstance(v, str) or len(v) > 4096
                                               or not _IMAGE_ID.fullmatch(v.rsplit("@", 1)[-1])
                                               for v in digests)):
                raise ValueError("image identity differs")
        except Exception as exc:
            raise BenchmarkJobError("swe_image_identity_mismatch", "selected cached SWE image is missing, changed, or unreadable") from exc
        observations.append({"instance_id": instance, "tag": tag,
                             "image_id": image_id, "repo_digests": sorted(digests)})
    return observations
