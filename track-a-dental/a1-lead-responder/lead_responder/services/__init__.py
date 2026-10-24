"""Layer 4b: services hold the business rules (what may be sent, when a lead counts as handled).

They decide; the repository reads and writes (one method = one transaction) and never decides.
Services never build SQL.
"""
