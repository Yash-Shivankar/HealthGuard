"""Business logic for the triage flow.

Views handle HTTP and nothing else; everything they call lives here.  Keeping
that boundary is what stops this collapsing back into the 193-line views.py
the audit found (AUDIT 2.14).
"""
