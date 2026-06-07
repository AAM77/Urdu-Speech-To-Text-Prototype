"""Infrastructure adapters for persistence, queues, storage, and providers."""

from urdu_pipeline.infrastructure.in_memory import (
    InMemoryMetadataStore,
    InMemoryObjectStore,
)

__all__ = ["InMemoryMetadataStore", "InMemoryObjectStore"]
