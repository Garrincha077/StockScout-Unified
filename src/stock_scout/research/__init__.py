"""Research-only, non-ranking analytics shared by scan surfaces."""

from stock_scout.research.ma_cluster_preferred import (
    PREFERRED_PROFILE_SOURCE,
    PREFERRED_PROFILE_VERSION,
    build_ma_cluster_research_profile,
    choose_ma_cluster_research_profile,
)

__all__ = [
    "PREFERRED_PROFILE_SOURCE",
    "PREFERRED_PROFILE_VERSION",
    "build_ma_cluster_research_profile",
    "choose_ma_cluster_research_profile",
]
