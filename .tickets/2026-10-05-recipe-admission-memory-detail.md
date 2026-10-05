# Show the memory facts behind Windows recipe admission refusal

Status: implemented; independent review and CI pending.

A bounded recipe load can fail after an earlier host-memory sample appeared sufficient. The generic Windows reserve error concealed whether the current free-memory comparison or the WSL ceiling comparison refused it. Separate samples can change and cannot reconstruct the admission observation.

Keep both existing comparisons and all reserves unchanged. Include the exact numeric byte observations and required bounds in the refusal from the same sample used for admission. Do not add retries or treat rounded displays as authority. Regression coverage checks both boundaries at equality and one KiB below, plus the existing higher declared-reserve case.
