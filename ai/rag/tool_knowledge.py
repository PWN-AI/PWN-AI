"""Read tool documentation from the single versioned database snapshot."""
from ai.rag.vector_store import DEFAULT_SNAPSHOT, read_snapshot


def get_tool_documents():
    return [
        {key: doc[key] for key in ("tool", "category", "content")}
        for doc in read_snapshot(DEFAULT_SNAPSHOT)["documents"]
    ]
