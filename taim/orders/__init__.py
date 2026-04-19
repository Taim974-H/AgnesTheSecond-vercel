"""Order placement module.

Persists chat + cube conversations, extracts order fields via an LLM, validates
them against the real supplier/product DB, and generates a minimal PDF PO that
renders in the right-hand panel of the chat UI.
"""
