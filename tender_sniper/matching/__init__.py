"""
Smart Matching Engine for tender-filter matching.

Example usage:
    from tender_sniper.matching import SmartMatcher

    # Create matcher
    matcher = SmartMatcher()

    # Match tender against filter
    result = matcher.match_tender(tender, filter_config)

    if result:
        print(f"Match score: {result['score']}/100")

    # Batch matching
    results = matcher.batch_match(tenders, filters, min_score=60)
"""

from .smart_matcher import SmartMatcher

__all__ = ['SmartMatcher']
