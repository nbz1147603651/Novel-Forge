"""Stage implementations for short story execution.

Each sub-module handles one phase of the short story pipeline:

- plan    : spec enrichment, blueprint generation, beats
- draft   : initial draft (single or segmented)
- edit    : multi-round edit loop with completeness checks
- evaluate: final evaluation
- creative: creative analysis summary
"""
