"""Print MCP: control Photoshop printing with a fixed colour pipeline.

Photoshop does the colour conversion into a chosen ICC profile, the printer
driver is told not to adjust colour, and folders can be printed as a batch.
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["icc", "jsx", "photoshop", "printers", "runner", "server"]
