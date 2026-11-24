"""Pluggable document parsers (docs/contracts.md §11). Every parser turns one stored upload
into ParsedChunks with source metadata and writes its artifacts into a staging directory.

Only `docling_runner` and `docling_mapping` may import Docling, and only the parse worker
process and tests import those modules: the web process stays free of Docling and torch."""
