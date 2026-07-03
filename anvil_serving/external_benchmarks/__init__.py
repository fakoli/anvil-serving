"""External benchmark ingestion and comparison helpers.

External rows are performance priors for capacity planning and candidate
ranking. They are not routing-quality gates.
"""

from . import normalize

__all__ = ["normalize"]

