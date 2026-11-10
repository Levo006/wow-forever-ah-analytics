"""Startskript: ``python run.py`` = Website starten (gleich wie ``python -m ahanalytics serve``).

Weitere Befehle: ``python run.py demo``, ``python run.py import-auctionator`` usw.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ahanalytics.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["serve"]))
