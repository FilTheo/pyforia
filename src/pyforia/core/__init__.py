"""
Core module for inventory management package.

Contains data structures and base policy class for the DataFrame-based
multi-SKU inventory management system.
"""

from .base_policy import BasePolicy
from .callbacks import (
    CallbackContext,
    CallbackError,
    InventoryAdjustmentResult,
    OrderAdjustmentResult,
    ScheduledInventoryAdjustment,
    ScheduledOrderHold,
    ScheduledOrderMultiplier,
    ScheduledOrderOverride,
    SimulationCallback,
)
from .data_structures import InventoryStateDataFrame, OrderDecision
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
from .simulation_engine import ComparisonResult, SimulationEngine, SimulationResult

__all__ = [
    "BasePolicy",
    "CallbackContext",
    "CallbackError",
    "ComparisonResult",
    "ConstraintContext",
    "ConstraintResult",
    "FIFOLotLedger",
    "InventoryAdjustmentResult",
    "InventoryStateDataFrame",
    "MaximumOrderQuantity",
    "MinimumOrderQuantity",
    "OrderAdjustmentResult",
    "OrderDecision",
    "OrderMultiple",
    "OrderingConstraint",
    "OrderingConstraints",
    "ScheduledInventoryAdjustment",
    "ScheduledOrderHold",
    "ScheduledOrderMultiplier",
    "ScheduledOrderOverride",
    "ShelfLifeEngine",
    "ShelfSpaceLimit",
    "SimulationCallback",
    "SimulationEngine",
    "SimulationResult",
]
