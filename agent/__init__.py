# Lazy import to avoid importing main (which requires langchain_openai) 
# when only schemas are needed (e.g., from backend)
# LangGraph references ./agent/main.py:cali_graph directly, so this __init__.py 
# doesn't need to export cali_graph
def __getattr__(name):
    if name == "cali_graph":
        from .main import cali_graph
        return cali_graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["cali_graph"]
