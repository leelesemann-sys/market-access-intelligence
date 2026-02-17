"""HTA Intelligence Pipeline Orchestration.

Usage:
    python run_pipeline.py --source gba        # Import G-BA data
    python run_pipeline.py --source nice       # Import NICE data
    python run_pipeline.py --source all        # Import all sources
    python run_pipeline.py --source gba --skip-download  # Use cached XML
"""

import argparse
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")


def run_gba(skip_download: bool = False):
    """Run G-BA pipeline: Download → Parse → Import."""
    from sources.gba import GBASource
    from pipeline.importer import import_records
    from pathlib import Path

    source = GBASource()

    if skip_download:
        xml_path = Path("../data/downloads/gba_beschluss_info.xml")
        if not xml_path.exists():
            log.error("Cached XML not found at %s, run without --skip-download first", xml_path)
            return
        log.info("Using cached XML: %s", xml_path)
        records = source._parse(xml_path)
    else:
        records = source.fetch()

    log.info("Importing %d G-BA records into database...", len(records))
    stats = import_records(records)
    return stats


def run_nice():
    """Run NICE pipeline: Download Excel → Parse → Import."""
    log.info("NICE source not yet implemented (Phase 1b)")
    return None


def main():
    parser = argparse.ArgumentParser(description="HTA Intelligence Pipeline")
    parser.add_argument(
        "--source",
        choices=["gba", "nice", "all"],
        required=True,
        help="Which data source to import",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Use cached download instead of re-downloading",
    )
    args = parser.parse_args()

    start = time.time()
    log.info("=" * 60)
    log.info("HTA Intelligence Pipeline — source: %s", args.source)
    log.info("=" * 60)

    if args.source in ("gba", "all"):
        stats = run_gba(skip_download=args.skip_download)
        if stats:
            log.info("G-BA stats: %s", stats)

    if args.source in ("nice", "all"):
        run_nice()

    elapsed = time.time() - start
    log.info("Pipeline finished in %.1f seconds", elapsed)


if __name__ == "__main__":
    main()
