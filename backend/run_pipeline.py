"""HTA Intelligence Pipeline Orchestration.

Usage:
    python run_pipeline.py --source gba        # Import G-BA data
    python run_pipeline.py --source nice       # Import NICE data
    python run_pipeline.py --source all        # Import all sources
    python run_pipeline.py --source gba --skip-download  # Use cached XML
    python run_pipeline.py --index             # Denormalize + Embed + Index
    python run_pipeline.py --source all --index  # Full pipeline
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


def run_nice(skip_download: bool = False):
    """Run NICE pipeline: Download Excel → Parse → Import."""
    from sources.nice import NICESource
    from pipeline.importer import import_records
    from pathlib import Path

    source = NICESource()

    if skip_download:
        excel_path = Path("../data/downloads/nice_ta_recommendations.xlsx")
        if not excel_path.exists():
            log.error("Cached Excel not found at %s, run without --skip-download first", excel_path)
            return
        log.info("Using cached Excel: %s", excel_path)
        records = source._parse(excel_path)
    else:
        records = source.fetch()

    log.info("Importing %d NICE records into database...", len(records))
    stats = import_records(records)
    return stats


def run_index():
    """Run denormalize → embed → index pipeline."""
    from pipeline.denormalizer import refresh as denormalize
    from pipeline.embedder import embed_and_collect
    from pipeline.indexer import create_index, upload_documents, mark_indexed

    # Step 1: Denormalize SQL → search_documents
    log.info("--- Denormalize ---")
    new_docs = denormalize()

    # Step 2: Create/update search index
    log.info("--- Create Index ---")
    create_index()

    # Step 3: Embed + collect documents with vectors
    log.info("--- Embed ---")
    docs = embed_and_collect(batch_size=100)

    if not docs:
        log.info("No new documents to index")
        return

    # Step 4: Upload to Azure AI Search
    log.info("--- Index ---")
    uploaded = upload_documents(docs)

    # Step 5: Mark as indexed in SQL
    doc_ids = [d["id"] for d in docs]
    mark_indexed(doc_ids)
    log.info("Indexing complete: %d documents", uploaded)


def main():
    parser = argparse.ArgumentParser(description="HTA Intelligence Pipeline")
    parser.add_argument(
        "--source",
        choices=["gba", "nice", "all"],
        default=None,
        help="Which data source to import",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Use cached download instead of re-downloading",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="Run denormalize → embed → index pipeline",
    )
    args = parser.parse_args()

    start = time.time()
    log.info("=" * 60)
    parts = []
    if args.source:
        parts.append(f"source={args.source}")
    if args.index:
        parts.append("index")
    log.info("HTA Intelligence Pipeline — %s", ", ".join(parts) or "no action")
    log.info("=" * 60)

    if args.source in ("gba", "all"):
        stats = run_gba(skip_download=args.skip_download)
        if stats:
            log.info("G-BA stats: %s", stats)

    if args.source in ("nice", "all"):
        stats = run_nice(skip_download=args.skip_download)
        if stats:
            log.info("NICE stats: %s", stats)

    if not args.source and not args.index:
        parser.error("At least one of --source or --index is required")

    if args.index:
        run_index()

    elapsed = time.time() - start
    log.info("Pipeline finished in %.1f seconds", elapsed)


if __name__ == "__main__":
    main()
