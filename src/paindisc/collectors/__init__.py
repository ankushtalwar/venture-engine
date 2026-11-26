from .base import CollectorUnavailable, Query, html_to_text
from .forums import BraveCollector, GitHubIssuesCollector, HNCollector
from .jobs import GreenhouseCollector, LeverCollector
from .pricing import PricingCollector, WaybackCollector

# Sources that run search queries chosen by the planner.
QUERY_COLLECTORS = {"hn": HNCollector, "github": GitHubIssuesCollector, "brave": BraveCollector}
# Sources driven by config lists (job boards, pricing targets).
BOARD_COLLECTORS = {"greenhouse": GreenhouseCollector, "lever": LeverCollector}

__all__ = [
    "BOARD_COLLECTORS",
    "QUERY_COLLECTORS",
    "BraveCollector",
    "CollectorUnavailable",
    "GitHubIssuesCollector",
    "GreenhouseCollector",
    "HNCollector",
    "LeverCollector",
    "PricingCollector",
    "Query",
    "WaybackCollector",
    "html_to_text",
]
