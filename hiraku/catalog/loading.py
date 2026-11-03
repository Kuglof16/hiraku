"""Who and what is on board: params["loading"] of a load case (catalog Q30).

The rule lives in the contract (hiraku.contract.loading) and every department
calls it; the catalog passes the names through.
"""

from __future__ import annotations

from hiraku.contract.loading import CARGO, LOADING_JA, OCCUPANTS, loading_of, on_board, parts_on_board

__all__ = ["CARGO", "LOADING_JA", "OCCUPANTS", "loading_of", "on_board", "parts_on_board"]
