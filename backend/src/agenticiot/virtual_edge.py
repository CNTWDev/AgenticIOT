"""Backward-compatible entry point; new integrations use agenticiot.edge."""

from agenticiot.edge import EdgeRuntime as VirtualEdge
from agenticiot.edge import NoRedirect, Transport, main

__all__ = ["NoRedirect", "Transport", "VirtualEdge", "main"]

if __name__ == "__main__":
    main()
