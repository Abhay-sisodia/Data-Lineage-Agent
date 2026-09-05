"""Name resolution against the data dictionary, at ingest.

Never trust the literal name in code. A synonym quietly redirects `dim_customer` to
`DW.DIM_CUSTOMER_V2`; an unqualified name resolves through the executing user's schema;
a view hides the base table that actually receives the write.

These are *silent* failures - the parser succeeds and binds the wrong object, and all
three triangulation witnesses then agree on the same wrong name. Evidence tiering offers
no protection against that, which is why the defence has to live here, at ingest, rather
than at scoring.

Resolving names here kills three of the eight known silent failures and is the
highest-value defensive step in the engine.
"""
