#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve everything that has played out: score due predictions + close out zone setups
(WIN/LOSS/EXPIRED). Run by the hourly systemd timer on the server."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backtest")))
import learning_store as LS
import setup_store as SU
import live_feed as F

if __name__ == "__main__":
    print("scored predictions:", LS.score_due(F.price_at))
    print("resolved setups   :", SU.resolve())
