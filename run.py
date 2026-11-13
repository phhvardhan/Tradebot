#!/usr/bin/env python3
"""Signal Desk. Start the paper-trading server on http://127.0.0.1:8765 (see README.md).

    python3 run.py            # all four markets
    python3 run.py --paused   # start with the agents paused
    python3 run.py --replay   # print the 5-year replay of the stock rule and exit
"""
from signaldesk.app import main

if __name__ == '__main__':
    main()
