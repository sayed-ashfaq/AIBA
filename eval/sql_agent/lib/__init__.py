"""Reusable pieces for the SQL-agent eval harness.

Kept free of any dependency on ``config`` so each module can be unit-tested on its own:

    compare   - result-set equivalence (the tricky part: order, tolerance, column order)
    logparse  - turn a slice of aiba.log into structured counts/timings
    dbio      - run read-only SQL with psycopg2 and normalise the cell values
    dataset   - load and validate the golden question YAML
"""
