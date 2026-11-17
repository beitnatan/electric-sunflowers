#!/usr/bin/env python3
"""Thin wrapper: tools/body_curve.py with --body guitar --norm 100 400.
usage: guitar_curve.py target|engine|row|check ... (see body_curve.py)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import body_curve

if __name__ == "__main__":
    body_curve.main(["--body", "guitar", "--norm", "100", "400"] + sys.argv[1:])
