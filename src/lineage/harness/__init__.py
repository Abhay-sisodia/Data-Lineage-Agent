"""The scoring harness.

Precision, recall and parse coverage against hand-labelled ground truth - scored per
band, never blended, because a single number hides which band broke.

Also reports the tier distribution: if most edges land in Tier C or D the lineage
technically works but the evidence story does not, and that is a finding in itself.
"""
