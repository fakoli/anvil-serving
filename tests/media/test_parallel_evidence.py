"""Published capacity headlines must be supported by the retained safe samples."""
import json
import math
from pathlib import Path


def test_parallel_capacity_projection_supports_headlines():
    path = Path(__file__).resolve().parents[2] / "docs/findings/2026-11-16-media-parallel-evidence/measurements.json"
    evidence = json.loads(path.read_text())
    assert evidence["nativeEvidence"] is False
    for case in evidence["cases"].values():
        samples = case["samples"]
        assert case["passed"] and case["outputCount"] == case["fullDecodeCount"] == 2
        assert case["peakGpuMiB"] == max(s["gpuUsedMiB"] for s in samples)
        assert case["minimumCommitFreeGiB"] == min(s["commitFreeGiB"] for s in samples)
        overlap = resident = 0
        for index, sample in enumerate(samples):
            assert sample["gpuFreeMiB"] >= 1024 and sample["commitFreeGiB"] >= 16 and sample["availableGiB"] >= 16
            assert sample["containerOom"] == sample["containerOomKill"] == 0
            if not index:
                continue
            previous = samples[index - 1]
            delta = sample["elapsedSeconds"] - previous["elapsedSeconds"]
            if (sample["nativeRunning"] == previous["nativeRunning"] == [1, 1]
                    and 0 < delta <= case["maximumSampleIntervalSeconds"]):
                overlap += delta
                if all(value > 2 * 1024**3 for s in (sample, previous) for value in s["cudaAllocatedBytes"]):
                    resident += delta
        assert math.isclose(case["overlapSeconds"], overlap, abs_tol=0.00001)
        assert math.isclose(case["bothResidentOverlapSeconds"], resident, abs_tol=0.00001)
        assert overlap >= 5 and resident >= 5
