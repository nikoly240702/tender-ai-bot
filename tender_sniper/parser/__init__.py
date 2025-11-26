"""
Real-time Parser for zakupki.gov.ru monitoring.

Example usage:
    from tender_sniper.parser import RealtimeParser

    # Create parser
    parser = RealtimeParser(poll_interval=300)  # 5 minutes

    # Add callback
    async def on_new_tenders(tenders):
        print(f"Found {len(tenders)} new tenders!")

    parser.add_callback(on_new_tenders)

    # Start monitoring
    await parser.start(
        keywords="компьютеры",
        price_min=100_000,
        price_max=5_000_000
    )
"""

from .realtime_parser import RealtimeParser

__all__ = ['RealtimeParser']
