"""Dataflow analysis over a procedure.

This is compiler dataflow analysis, not SQL parsing: control flow graph, def-use chains
over variables and temp tables treated as memory locations, branch guards carried onto
edges, and interprocedural summaries with a declared depth cap.

Anything the analysis cannot resolve is routed to the abstention classifier and declared.
No guessed edges, ever.
"""
