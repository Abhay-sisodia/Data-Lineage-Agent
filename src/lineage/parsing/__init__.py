"""Parsing: two tools with a strict division of labour.

ANTLR understands the *program* - statement boundaries, declared variables, control
flow - and captures each SQL fragment as text without trying to understand it.

SQLGlot understands one *statement* - columns, joins, filters - and knows nothing about
variables or anything spanning statements.

Neither is a substitute for the other. Reaching for a SQL parser to analyse a procedure
is the mistake this whole spike exists to avoid.
"""
