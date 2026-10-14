"""High-level RAG retriever for ReAct agent consumption.

Provides a simple query interface that returns formatted context strings
ready to be injected into LLM reasoning scratchpads.
"""

from __future__ import annotations

from typing import List, Optional

from ai.rag.vector_store import ToolVectorStore


class ToolRetriever:
    """Retrieves tool knowledge from the FAISS vector store."""

    def __init__(self, store: ToolVectorStore):
        self.store = store

    def retrieve(
        self,
        query: str,
        top_k: int = 4,
        tool_filter: Optional[str] = None,
    ) -> str:
        """Retrieve relevant tool documentation for the given query.

        Returns a formatted string suitable for LLM consumption.
        """
        if not self.store.is_ready:
            raise RuntimeError("RAG knowledge base not initialized.")

        results = self.store.search(query, top_k=top_k, tool_filter=tool_filter)
        if not results:
            raise LookupError(f"No relevant documentation found for: {query}")

        parts: List[str] = []
        parts.append(f"=== Retrieved Tool Documentation ({len(results)} results) ===")
        for i, (doc, score) in enumerate(results, 1):
            parts.append(f"\n--- Result {i} (relevance: {score:.3f}) ---")
            parts.append(f"Tool: {doc['tool']} | Category: {doc['category']}")
            parts.append(doc["content"])
        parts.append("\n=== End Retrieved Documentation ===")

        return "\n".join(parts)

    def retrieve_for_tool(self, tool_name: str, query: str, top_k: int = 3) -> str:
        """Retrieve documentation specifically for one tool."""
        return self.retrieve(query, top_k=top_k, tool_filter=tool_name)

    def retrieve_all_for_tool(self, tool_name: str) -> str:
        """Retrieve ALL documentation for a specific tool."""
        if not self.store.is_ready:
            raise RuntimeError("RAG knowledge base not initialized.")

        docs = [
            doc for doc in self.store._documents
            if doc["tool"] == tool_name
        ]
        if not docs:
            raise LookupError(f"No documentation found for tool: {tool_name}")

        parts: List[str] = [f"=== Complete Documentation for {tool_name} ==="]
        for doc in docs:
            parts.append(f"\n--- {doc['category']} ---")
            parts.append(doc["content"])
        parts.append(f"\n=== End {tool_name} Documentation ===")

        return "\n".join(parts)
