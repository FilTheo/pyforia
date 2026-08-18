"""
Core module for inventory management package.

Contains data structures and base policy class for the DataFrame-based
multi-SKU inventory management system.
"""

from .data_structures import InventoryStateDataFrame, OrderDecision
from .base_policy import BasePolicy
from .simulation_engine import SimulationEngine, SimulationResult, ComparisonResult
from .order_constraints import (
    ConstraintContext,
    ConstraintResult,
    MaximumOrderQuantity,
    MinimumOrderQuantity,
    OrderingConstraint,
    OrderingConstraints,
    OrderMultiple,
    ShelfSpaceLimit,
)
from .shelf_life import FIFOLotLedger, ShelfLifeEngine

__all__ = [
    "InventoryStateDataFrame",
    "OrderDecision",
    "ConstraintContext",
    "ConstraintResult",
    "MaximumOrderQuantity",
    "MinimumOrderQuantity",
    "OrderingConstraint",
    "OrderingConstraints",
    "OrderMultiple",
    "ShelfSpaceLimit",
    "BasePolicy",
    "SimulationEngine",
    "SimulationResult",
    "ComparisonResult",
    "FIFOLotLedger",
    "ShelfLifeEngine",
]
