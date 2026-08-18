"""
SimulationEngine for the DataFrame-based multi-SKU inventory management system.

This module provides:
    - SimulationEngine: Orchestrates multi-period inventory simulation (like PyTorch DataLoader)
    - SimulationResult: Container for simulation outputs with summary statistics

The engine wraps the existing simulation primitives (process_demand, update_inventory_with_orders,
policy.predict) into an automated loop. Users can subclass SimulationEngine and override the
documented pre-finalization hooks for custom behavior.

Usage:
    # Simple usage
    engine = SimulationEngine()
    result = engine.run(
        policy=policy,
        demand_source=demand_df,
        inventory=inventory,
        n_periods=365,
        period_frequency="D",
        initial_decision="none",
        warmup_periods=0,
        scoring_periods=365,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="example_demand",
        random_seed=None,
    )
    print(result.summary())

    # Custom subclass
    class MyEngine(SimulationEngine):
        def on_stockout(self, inventory, period):
            print(f"Stockout at period {period}!")
"""

import copy
import hashlib
import importlib.metadata
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Union, Callable, Dict, List, Mapping, Optional
from uuid import uuid4

import numpy as np
import pandas as pd

from pyforia.core.data_structures import (
    InventoryStateDataFrame,
    _identifier_sample,
    _require_forward_frequency,
    _require_identifiers,
)
from pyforia.core.base_policy import BasePolicy
from pyforia.core.order_constraints import ConstraintContext, OrderingConstraints


# ============================================================================
# SIMULATION RESULT
# ============================================================================

class SimulationResult:
    """
    Container for simulation outputs.

    Attributes:
        history: DataFrame of all inventory states across all periods (from get_history())
        inventory: Final InventoryStateDataFrame after simulation
        n_periods: Number of periods simulated
        policy_name: Name of the policy used

    Methods:
        summary(): Returns explicitly named demand, stock, and order statistics
    """

    def __init__(
        self,
        history: pd.DataFrame,
        inventory: InventoryStateDataFrame,
        n_periods: int,
        policy_name: str,
        event_frame: Optional[pd.DataFrame] = None,
        run_settings: Optional[dict] = None,
        run_manifest: Optional[dict] = None,
    ):
        self.history = history
        self.inventory = inventory
        self.n_periods = n_periods
        self.policy_name = policy_name
        self.run_settings = dict(run_settings or {})
        self.run_manifest = dict(run_manifest or {})
        self._event_frame = (
            event_frame.copy()
            if event_frame is not None
            else pd.DataFrame()
        )

    def to_event_frame(self, window: Optional[str] = None) -> pd.DataFrame:
        """
        Return the normalized simulation event table.

        The event table stores one row per SKU and simulated period with
        additive operational quantities that can be safely aggregated later.
        """
        result = self._event_frame.copy()
        if window is None or window == "all":
            return result
        if window not in {"warmup", "scoring", "settlement"}:
            raise ValueError("window must be 'all', 'warmup', 'scoring', or 'settlement'")
        if "run_window" not in result.columns:
            raise ValueError("event frame does not contain run-window metadata")
        return result[result["run_window"] == window].copy()

    def summary(self) -> Dict:
        """
        Compute a small set of explicitly named summary statistics.

        Returns:
            dict with keys:
                - fill_rate: Fraction of demand satisfied (1 - shortage/demand).
                    Computed from backorder increases per SKU per period.
                    For lost-sales model (backorders always 0), falls back to service_level.
                - demand_period_service_level: Fraction of positive-demand
                    SKU-period rows without shortage. This is not cycle service.
                - mean_ending_on_hand_per_sku_period: Mean ending on-hand at
                    the SKU-period row grain.
                - stockout_periods: Number of periods where at least one SKU had on_hand == 0.
                - total_order_units: Sum of all order quantities placed.
        """
        e = self.to_event_frame(window="scoring")
        if e.empty:
            return {
                'fill_rate': 1.0,
                'demand_period_service_level': 1.0,
                'mean_ending_on_hand_per_sku_period': 0.0,
                'stockout_periods': 0,
                'total_order_units': 0.0,
                'order_event_count': 0,
                'sku_order_line_count': 0,
            }

        period_events = e[e['event_type'] == 'period']
        if period_events.empty:
            raise ValueError("scoring event frame does not contain period events")

        required_numbers = {
            "period events": (
                period_events,
                ['demand', 'shortage_units', 'ending_on_hand'],
            ),
            "scoring events": (
                e,
                ['order_quantity', 'order_event_count', 'sku_order_line_count'],
            ),
        }
        for label, (frame, columns) in required_numbers.items():
            missing = sorted(set(columns) - set(frame.columns))
            if missing:
                raise ValueError(f"{label} are missing columns: {missing}")
            numbers = frame[columns].apply(pd.to_numeric, errors='coerce')
            if numbers.isna().any().any() or not np.isfinite(numbers.to_numpy()).all():
                raise ValueError(f"{label} must contain complete finite numeric values")
            if (numbers < 0).any().any():
                raise ValueError(f"{label} must contain non-negative numeric values")
        if 'stockout_flag' not in period_events.columns:
            raise ValueError("period events are missing columns: ['stockout_flag']")
        if period_events['stockout_flag'].isna().any():
            raise ValueError("period events must contain complete stockout flags")

        total_demand = period_events['demand'].sum()
        total_shortage = period_events['shortage_units'].sum()
        fill_rate = 1.0 if total_demand <= 0 else max(0.0, 1.0 - (total_shortage / total_demand))

        eligible = period_events[period_events['demand'] > 0]
        demand_period_service = 1.0 if eligible.empty else float(
            (eligible['shortage_units'] <= 0).mean()
        )

        stockout_periods = int(
            (period_events.groupby('demand_period')['stockout_flag'].any()).sum()
        )
        return {
            'fill_rate': fill_rate,
            'demand_period_service_level': demand_period_service,
            'mean_ending_on_hand_per_sku_period': period_events['ending_on_hand'].mean(),
            'stockout_periods': stockout_periods,
            'total_order_units': e['order_quantity'].sum(),
            'order_event_count': int(e['order_event_count'].sum()),
            'sku_order_line_count': int(e['sku_order_line_count'].sum()),
        }

    def __repr__(self) -> str:
        return (
            f"SimulationResult(policy={self.policy_name}, "
            f"n_periods={self.n_periods}, "
            f"history_rows={len(self.history)})"
        )


def _sum_in_transit(value) -> float:
    """Return the total pipeline inventory for a row."""
    if hasattr(value, "sum"):
        return float(value.sum())
    return 0.0


def _build_period_event_frame(
    inventory_before: InventoryStateDataFrame,
    inventory_after_demand: InventoryStateDataFrame,
    inventory_after_orders: InventoryStateDataFrame,
    policy: BasePolicy,
    demand_period: int,
    order_event_count: int,
    sku_order_line_counts: Mapping[str, int],
    order_line_quantity_squared_sums: Mapping[str, float],
) -> pd.DataFrame:
    """
    Normalize one simulation step to one event row per SKU.

    The event frame is the evaluation source of truth for inventory metrics.
    """
    before_df = inventory_before.get_dataframe().copy()
    after_demand_df = inventory_after_demand.get_dataframe().copy()
    after_orders_df = inventory_after_orders.get_dataframe().copy()

    sku_column = inventory_before.sku_column
    before_df['starting_on_order'] = before_df['in_transit'].apply(_sum_in_transit)
    after_orders_df['on_order_end'] = after_orders_df['in_transit'].apply(_sum_in_transit)

    event_df = before_df[[
        sku_column,
        'on_hand',
        'safety_stock',
        'backorders',
        'starting_on_order',
    ]].merge(
        after_demand_df[[
            sku_column,
            'period',
            'date',
            'on_hand',
            'backorders',
            'latest_received',
            'latest_fulfilled',
            'latest_backorders_fulfilled',
            'latest_incoming_demand',
            'latest_shortage',
            'is_review_period',
        ]],
        on=sku_column,
        how='left',
        suffixes=('_start', '_end'),
    ).merge(
        after_orders_df[[
            sku_column,
            'target_level',
            'latest_order',
            'on_order_end',
        ]],
        on=sku_column,
        how='left',
        suffixes=('', '_post_order'),
    )

    event_df = event_df.rename(
        columns={
            sku_column: 'unique_id',
            'on_hand_start': 'starting_on_hand',
            'backorders_start': 'starting_backorders',
            'on_hand_end': 'ending_on_hand',
            'backorders': 'backorders_end',
            'latest_incoming_demand': 'demand',
            'latest_shortage': 'shortage_units',
            'latest_received': 'received_units',
            'latest_fulfilled': 'fulfilled_units',
            'latest_backorders_fulfilled': 'backorders_fulfilled',
            'latest_order': 'order_quantity',
        }
    )

    numeric_columns = [
        'starting_on_hand',
        'starting_backorders',
            'starting_on_order',
            'safety_stock',
        'ending_on_hand',
        'backorders_end',
        'demand',
        'received_units',
        'fulfilled_units',
        'backorders_fulfilled',
        'shortage_units',
        'order_quantity',
        'target_level',
        'safety_stock',
        'on_order_end',
    ]
    for column in numeric_columns:
        event_df[column] = pd.to_numeric(event_df[column], errors='coerce')

    event_df['policy'] = policy.policy_name
    event_df['event_type'] = 'period'
    event_df['demand_period'] = pd.Series(
        [demand_period] * len(event_df),
        index=event_df.index,
        dtype="Int64",
    )
    event_df['date'] = pd.to_datetime(event_df['date']).astype("datetime64[ns]")
    event_df['allow_backorders'] = policy.allow_backorders
    event_df['decision_flag'] = event_df['is_review_period'].fillna(False).astype(bool)
    event_df['inventory_adjustment_units'] = 0.0
    event_df['expired_units'] = 0.0
    event_df['backorder_increment'] = 0.0
    if policy.allow_backorders:
        event_df['backorder_increment'] = event_df['shortage_units'].fillna(0.0)
    event_df['lost_sales_units'] = 0.0
    if not policy.allow_backorders:
        event_df['lost_sales_units'] = event_df['shortage_units'].fillna(0.0)
    event_df['inventory_position_end'] = (
        event_df['ending_on_hand'].fillna(0.0)
        + event_df['on_order_end'].fillna(0.0)
        - event_df['backorders_end'].fillna(0.0)
    )
    event_df['stockout_flag'] = event_df['shortage_units'].fillna(0.0) > 0
    event_df['backorder_flag'] = event_df['backorders_end'].fillna(0.0) > 0
    event_df['sku_order_line_count'] = (
        event_df['unique_id'].map(sku_order_line_counts).fillna(0).astype(int)
    )
    event_df['order_line_quantity_squared_sum'] = (
        event_df['unique_id']
        .map(order_line_quantity_squared_sums)
        .fillna(0.0)
        .astype(float)
    )
    event_df['order_event_count'] = 0
    if len(event_df):
        # Store the period-level count once so it remains additive across SKUs.
        event_df.loc[event_df.index[0], 'order_event_count'] = order_event_count

    ordered_columns = [
        'unique_id',
        'event_type',
        'demand_period',
        'period',
        'date',
        'policy',
        'allow_backorders',
        'is_review_period',
        'decision_flag',
        'starting_on_hand',
        'starting_backorders',
        'starting_on_order',
        'received_units',
        'demand',
        'fulfilled_units',
        'backorders_fulfilled',
        'shortage_units',
        'lost_sales_units',
        'backorder_increment',
        'ending_on_hand',
        'backorders_end',
        'on_order_end',
        'inventory_position_end',
        'order_quantity',
        'order_event_count',
        'sku_order_line_count',
        'order_line_quantity_squared_sum',
        'expired_units',
        'inventory_adjustment_units',
        'target_level',
        'safety_stock',
        'stockout_flag',
        'backorder_flag',
    ]
    return event_df[ordered_columns]


def _build_initial_decision_event(
    inventory_before: InventoryStateDataFrame,
    inventory_after: InventoryStateDataFrame,
    policy: BasePolicy,
    order_event_count: int,
    sku_order_line_counts: Mapping[str, int],
    order_line_quantity_squared_sums: Mapping[str, float],
) -> pd.DataFrame:
    """Record a time-zero order as an explicit, demand-free event."""
    sku_column = inventory_before.sku_column
    before_df = inventory_before.get_dataframe().copy()
    after_df = inventory_after.get_dataframe().copy()
    before_df['starting_on_order'] = before_df['in_transit'].apply(_sum_in_transit)
    after_df['on_order_end'] = after_df['in_transit'].apply(_sum_in_transit)

    event_df = before_df[[
        sku_column,
        'period',
        'date',
        'on_hand',
        'backorders',
        'starting_on_order',
        'safety_stock',
    ]].merge(
        after_df[[
            sku_column,
            'on_hand',
            'backorders',
            'on_order_end',
            'latest_order',
            'target_level',
        ]],
        on=sku_column,
        how='left',
        suffixes=('_start', '_end'),
    ).rename(columns={
        sku_column: 'unique_id',
        'on_hand_start': 'starting_on_hand',
        'on_hand_end': 'ending_on_hand',
        'backorders_start': 'starting_backorders',
        'backorders_end': 'backorders_end',
        'latest_order': 'order_quantity',
    })
    event_df['event_type'] = 'initial_decision'
    event_df['demand_period'] = pd.Series(
        [pd.NA] * len(event_df),
        index=event_df.index,
        dtype="Int64",
    )
    event_df['date'] = pd.to_datetime(event_df['date']).astype("datetime64[ns]")
    event_df['policy'] = policy.policy_name
    event_df['allow_backorders'] = policy.allow_backorders
    event_df['is_review_period'] = True
    event_df['decision_flag'] = True
    for column in [
        'received_units',
        'demand',
        'fulfilled_units',
        'backorders_fulfilled',
        'shortage_units',
        'lost_sales_units',
        'backorder_increment',
        'expired_units',
        'inventory_adjustment_units',
    ]:
        event_df[column] = 0.0
    event_df['inventory_position_end'] = (
        event_df['ending_on_hand']
        + event_df['on_order_end']
        - event_df['backorders_end']
    )
    event_df['stockout_flag'] = False
    event_df['backorder_flag'] = event_df['backorders_end'] > 0
    event_df['sku_order_line_count'] = (
        event_df['unique_id'].map(sku_order_line_counts).fillna(0).astype(int)
    )
    event_df['order_line_quantity_squared_sum'] = (
        event_df['unique_id']
        .map(order_line_quantity_squared_sums)
        .fillna(0.0)
        .astype(float)
    )
    event_df['order_event_count'] = 0
    if len(event_df):
        event_df.loc[event_df.index[0], 'order_event_count'] = order_event_count
    ordered_columns = [
        'unique_id',
        'event_type',
        'demand_period',
        'period',
        'date',
        'policy',
        'allow_backorders',
        'is_review_period',
        'decision_flag',
        'starting_on_hand',
        'starting_backorders',
        'starting_on_order',
        'received_units',
        'demand',
        'fulfilled_units',
        'backorders_fulfilled',
        'shortage_units',
        'lost_sales_units',
        'backorder_increment',
        'ending_on_hand',
        'backorders_end',
        'on_order_end',
        'inventory_position_end',
        'order_quantity',
        'order_event_count',
        'sku_order_line_count',
        'order_line_quantity_squared_sum',
        'expired_units',
        'inventory_adjustment_units',
        'target_level',
        'safety_stock',
        'stockout_flag',
        'backorder_flag',
    ]
    return event_df[ordered_columns]


def _assert_event_flow_balance(event_df: pd.DataFrame) -> None:
    """Assert physical-stock, backlog, and pipeline balances per event row."""
    physical_expected = (
        event_df['starting_on_hand']
        + event_df['received_units']
        - event_df['backorders_fulfilled']
        - event_df['fulfilled_units']
        - event_df['expired_units']
        + event_df['inventory_adjustment_units']
    )
    backlog_expected = (
        event_df['starting_backorders']
        + event_df['backorder_increment']
        - event_df['backorders_fulfilled']
    )
    pipeline_expected = (
        event_df['starting_on_order']
        - event_df['received_units']
        + event_df['order_quantity']
    )
    checks = [
        ('physical inventory', physical_expected, event_df['ending_on_hand']),
        ('backlog', backlog_expected, event_df['backorders_end']),
        ('pipeline', pipeline_expected, event_df['on_order_end']),
    ]
    for name, expected, actual in checks:
        valid = np.isclose(expected.astype(float), actual.astype(float), rtol=0.0, atol=1e-9)
        if not valid.all():
            row = event_df.loc[~valid].iloc[0]
            raise AssertionError(
                f"{name} flow balance failed for SKU {row['unique_id']} at "
                f"period {row['period']}"
            )


# ============================================================================
# COMPARISON RESULT
# ============================================================================

class ComparisonResult:
    """
    Container holding multiple SimulationResults for side-by-side comparison.

    Attributes:
        results: Dict mapping label -> SimulationResult

    Usage:
        comp = engine.run_comparison(policies=[p1, p2], ...)
        print(comp.summary())           # DataFrame with one row per policy
        result_a = comp["Policy A"]     # Access individual SimulationResult
    """

    def __init__(self, results: Dict[str, SimulationResult]):
        self.results = results

    def summary(self) -> pd.DataFrame:
        """Return DataFrame with one row per policy, columns = summary metrics."""
        rows = []
        for name, result in self.results.items():
            s = result.summary()
            s['policy'] = name
            rows.append(s)
        return pd.DataFrame(rows).set_index('policy')

    def __getitem__(self, key: str) -> SimulationResult:
        return self.results[key]

    def __iter__(self):
        return iter(self.results)

    def __len__(self):
        return len(self.results)

    def __repr__(self) -> str:
        return f"ComparisonResult(policies={list(self.results.keys())})"


# ============================================================================
# SIMULATION ENGINE
# ============================================================================

class SimulationEngine:
    """
    Orchestrates multi-period inventory simulation for the DataFrame API.

    Like PyTorch's DataLoader: simple for standard use, subclassable for custom behavior.

    The engine wraps existing primitives — it calls process_demand(),
    checks is_review_period, calls policy.predict(), and calls
    update_inventory_with_orders(). It does NOT reimplement any of these.

    Usage:
        # Standard simulation
        engine = SimulationEngine()
        result = engine.run(
            policy=policy,              # Fitted BasePolicy subclass
            demand_source=demand_df,    # DataFrame with period column, or callable
            inventory=inventory,        # Initialized InventoryStateDataFrame
            n_periods=365,
            period_frequency="D",      # Explicit calendar frequency
            initial_decision="none",   # Or "before_first_demand"
            warmup_periods=0,
            scoring_periods=365,
            settlement_periods=0,
            order_during_settlement=False,
            demand_source_name="example_demand",
            random_seed=None,
        )

        # Custom simulation via subclassing
        class MyEngine(SimulationEngine):
            def on_review_period(self, inventory, policy, period):
                return super().on_review_period(inventory, policy, period)

            def on_stockout(self, inventory, period):
                print(f"Stockout at period {period}!")

    Hooks (override in subclass):
        before_step(inventory, period)  — called before each period
        on_review_period(inventory, policy, period) → inventory — ordering logic
        on_stockout(inventory, period)  — called when any SKU has stockout
    """

    def __init__(self, verbose: int = 0):
        """
        Args:
            verbose: Logging verbosity level.
                0 = silent (default), 1 = basic (start/end/milestones), 2 = full (per-period detail)
        """
        self.verbose = verbose

    def _log(self, msg: str, level: int = 1):
        """Print msg if self.verbose >= level."""
        if self.verbose >= level:
            print(msg)

    def run(
        self,
        policy: BasePolicy,
        demand_source: Union[pd.DataFrame, Callable],
        inventory: InventoryStateDataFrame,
        n_periods: int,
        *,
        period_frequency: str,
        initial_decision: str,
        warmup_periods: int,
        scoring_periods: int,
        settlement_periods: int,
        order_during_settlement: bool,
        demand_source_name: str,
        random_seed: Optional[int],
        policy_schedule: Optional[Mapping[int, BasePolicy]] = None,
        order_constraints: Optional[OrderingConstraints] = None,
    ) -> SimulationResult:
        """
        Run a multi-period inventory simulation.

        Args:
            policy: Fitted BasePolicy subclass (e.g., OrderUpToPolicy, ContinuousReviewPolicy).
            demand_source: Either a DataFrame with columns [unique_id, y, period] containing
                demand for all periods, or a callable(period) -> demand_df.
            inventory: Initialized InventoryStateDataFrame.
            n_periods: Number of periods to simulate.
            period_frequency: Explicit pandas frequency for one period.
            initial_decision: ``"before_first_demand"`` or ``"none"``.
            warmup_periods: State-advancing periods excluded from scoring.
            scoring_periods: Periods included in the default result summary.
            settlement_periods: Tail periods excluded from scoring.
            order_during_settlement: Whether review decisions remain active in
                the settlement tail.
            demand_source_name: Non-empty experiment identifier for demand.
            random_seed: Demand/scenario seed, or explicit ``None`` when no
                random generator is involved.
            policy_schedule: Optional mapping from decision period to a fitted
                policy snapshot. Snapshots must have the same policy class and
                operating configuration as ``policy``. This supports rolling-
                origin targets calculated outside the simulator.
            order_constraints: Optional explicit operational constraints.

        Returns:
            SimulationResult with history, final inventory state, and summary statistics.

        Example:
            engine = SimulationEngine()
            result = engine.run(policy=policy, demand_source=demand_df,
                                inventory=inventory, n_periods=30,
                                period_frequency="D", initial_decision="none",
                                warmup_periods=0, scoring_periods=30,
                                settlement_periods=0,
                                order_during_settlement=False,
                                demand_source_name="example_demand",
                                random_seed=None)
            print(result.summary())
        """
        if not isinstance(n_periods, int) or isinstance(n_periods, bool) or n_periods < 0:
            raise ValueError("n_periods must be a non-negative integer")
        window_lengths = {
            'warmup_periods': warmup_periods,
            'scoring_periods': scoring_periods,
            'settlement_periods': settlement_periods,
        }
        for name, value in window_lengths.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be an integer >= 0")
        if scoring_periods < 1:
            raise ValueError("scoring_periods must be an integer >= 1")
        if sum(window_lengths.values()) != n_periods:
            raise ValueError(
                "warmup_periods + scoring_periods + settlement_periods must equal n_periods"
            )
        if not isinstance(order_during_settlement, bool):
            raise ValueError("order_during_settlement must be boolean")
        if not isinstance(demand_source_name, str) or not demand_source_name.strip():
            raise ValueError("demand_source_name must be a non-empty string")
        if random_seed is not None and (
            not isinstance(random_seed, int) or isinstance(random_seed, bool)
        ):
            raise ValueError("random_seed must be an integer or explicit None")
        if order_constraints is not None and not isinstance(order_constraints, OrderingConstraints):
            raise TypeError("order_constraints must be an OrderingConstraints instance")
        constraint_manifest = (
            order_constraints.to_manifest() if order_constraints is not None else None
        )
        if not isinstance(policy, BasePolicy) or not policy.fitted_:
            raise ValueError("policy must be fitted before simulation")
        if not isinstance(inventory, InventoryStateDataFrame):
            raise TypeError("inventory must be an InventoryStateDataFrame")
        period_offset = _require_forward_frequency(
            period_frequency,
            "period_frequency",
        )
        if initial_decision not in {"none", "before_first_demand"}:
            raise ValueError(
                "initial_decision must be 'none' or 'before_first_demand'"
            )
        # A run owns its state. Caller state and pre-run history remain
        # untouched, while result.history contains snapshots from this run.
        inventory = copy.deepcopy(inventory)
        inventory.clear_history()
        inventory.allow_backorders = policy.allow_backorders
        inventory._validate_ready_state()
        opening_inventory_fingerprint = self._inventory_fingerprint(inventory)
        opening_period = int(inventory.get_dataframe()['period'].iloc[0])
        opening_date = pd.Timestamp(inventory.get_dataframe()['date'].iloc[0])
        self._validate_policy_information_origin(
            policy,
            latest_allowed_origin=opening_date,
            exact=False,
            expected_frequency=period_offset,
        )
        policy_schedule = self._validate_policy_schedule(
            policy,
            policy_schedule,
            opening_period=opening_period,
            n_periods=n_periods,
            initial_decision=initial_decision,
            opening_date=opening_date,
            period_offset=period_offset,
        )

        # Deferred import to avoid circular dependency (core ↔ utils)
        from pyforia.utils.inventory_operations import process_demand, update_inventory_with_orders
        self._process_demand = process_demand
        self._update_inventory_primitive = update_inventory_with_orders
        self._update_inventory = self._tracked_inventory_update

        demand_source_type = "dataframe" if isinstance(demand_source, pd.DataFrame) else "callable"
        demand_data = self._materialize_demand_source(demand_source, n_periods)
        demand_data = self._validate_demand_calendar(
            demand_data,
            inventory,
            n_periods,
            period_offset,
        )
        demand_fn = self._resolve_demand_source(demand_data)
        self._active_order_constraints = copy.deepcopy(order_constraints)
        if self._active_order_constraints is not None:
            self._active_order_constraints.reset(ConstraintContext(
                inventory=inventory,
                policy=policy,
                decision_period=opening_period,
            ))

        active_policy = copy.deepcopy(policy)
        update_log = []
        event_frames = []
        if initial_decision == "before_first_demand":
            inventory_before_initial_decision = copy.deepcopy(inventory)
            self._begin_order_capture()
            active_policy = self._policy_for_decision(
                active_policy,
                policy_schedule,
                opening_period,
                update_log,
            )
            inventory = self.on_review_period(inventory, active_policy, opening_period)
            initial_event = _build_initial_decision_event(
                inventory_before_initial_decision,
                inventory,
                active_policy,
                self._captured_order_event_count,
                self._captured_sku_order_line_counts,
                self._captured_order_line_quantity_squared_sums,
            )
            initial_event['run_window'] = 'warmup' if warmup_periods else 'scoring'
            initial_event = self._attach_order_audit(initial_event)
            _assert_event_flow_balance(initial_event)
            event_frames.append(initial_event)

        n_skus = len(inventory.get_dataframe())
        self._log(
            f"[SimEngine] Starting: {n_periods} periods, "
            f"policy={policy.policy_name}, {n_skus} SKUs"
        )
        milestones = {n_periods // 4, n_periods // 2, 3 * n_periods // 4}

        for period in range(n_periods):
            demand_df = demand_fn(period)
            run_window = self._run_window(
                period,
                warmup_periods,
                scoring_periods,
            )

            inventory_period_opening = copy.deepcopy(inventory)
            self.before_step(inventory, period)
            inventory = self.before_demand(inventory, demand_df, period)

            # Advance time: increments period, processes deliveries, applies demand
            inventory_after_demand = self._process_demand(
                inventory,
                demand_df,
                review_period=policy.review_period,
                period_frequency=period_offset.freqstr,
            )
            inventory = inventory_after_demand

            # Use actual inventory period (process_demand advances it)
            inv_df = inventory.get_dataframe()
            sim_period = int(inv_df['period'].iloc[0])

            # Check for stockout
            if inventory.has_stockout:
                self.on_stockout(inventory, sim_period)

            # Order on review periods
            decision_enabled = run_window != 'settlement' or order_during_settlement
            self._begin_order_capture()
            if inv_df['is_review_period'].iloc[0] and decision_enabled:
                active_policy = self._policy_for_decision(
                    active_policy,
                    policy_schedule,
                    sim_period,
                    update_log,
                )
                inventory = self.on_review_period(inventory, active_policy, sim_period)

            period_event = _build_period_event_frame(
                inventory_before=inventory_period_opening,
                inventory_after_demand=inventory_after_demand,
                inventory_after_orders=inventory,
                policy=active_policy,
                demand_period=period,
                order_event_count=self._captured_order_event_count,
                sku_order_line_counts=self._captured_sku_order_line_counts,
                order_line_quantity_squared_sums=(
                    self._captured_order_line_quantity_squared_sums
                ),
            )
            period_event['run_window'] = run_window
            period_event = self._attach_order_audit(period_event)
            period_event = self.after_period_event(period_event, inventory, sim_period)
            _assert_event_flow_balance(period_event)
            event_frames.append(period_event)

            # Verbose logging
            if period in milestones:
                pct = int(100 * (period + 1) / n_periods)
                self._log(f"[SimEngine] Period {period + 1}/{n_periods} ({pct}%)")

            if self.verbose >= 2:
                total_demand = inv_df['latest_incoming_demand'].sum()
                total_orders = inv_df['latest_order'].sum()
                total_on_hand = inv_df['on_hand'].sum()
                stockout_flag = 'YES' if inventory.has_stockout else 'no'
                self._log(
                    f"  Period {sim_period}: demand={total_demand:.0f}, "
                    f"orders={total_orders:.0f}, on_hand={total_on_hand:.0f}, "
                    f"stockout={stockout_flag}",
                    level=2,
                )

        history = inventory.get_history()
        resolved_commit = self._repository_commit()
        run_settings = {
            'period_frequency': period_offset.freqstr,
            'initial_decision': initial_decision,
            'input_period_convention': 'zero_based',
            'event_period_convention': 'opening_period_plus_one',
            'warmup_periods': warmup_periods,
            'scoring_periods': scoring_periods,
            'settlement_periods': settlement_periods,
            'order_during_settlement': order_during_settlement,
            'policy_update_periods': sorted(policy_schedule),
            'policy_update_log': update_log,
            'order_constraints': copy.deepcopy(constraint_manifest),
        }
        run_manifest = self._build_run_manifest(
            demand_data=demand_data,
            demand_source_name=demand_source_name.strip(),
            demand_source_type=demand_source_type,
            random_seed=random_seed,
            source_commit=resolved_commit,
            policy=policy,
            sku_column=inventory.sku_column,
            run_settings=run_settings,
            opening_inventory=opening_inventory_fingerprint,
        )
        result = SimulationResult(
            history=history,
            inventory=inventory,
            n_periods=n_periods,
            policy_name=policy.policy_name,
            event_frame=pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame(),
            run_settings=run_settings,
            run_manifest=run_manifest,
        )

        if self.verbose >= 1:
            s = result.summary()
            self._log(
                f"[SimEngine] Complete. fill_rate={s['fill_rate']:.3f}, "
                f"demand_period_service_level={s['demand_period_service_level']:.3f}, "
                "mean_ending_on_hand_per_sku_period="
                f"{s['mean_ending_on_hand_per_sku_period']:.1f}, "
                f"stockout_periods={s['stockout_periods']}"
            )

        return result

    @staticmethod
    def _run_window(period: int, warmup_periods: int, scoring_periods: int) -> str:
        if period < warmup_periods:
            return 'warmup'
        if period < warmup_periods + scoring_periods:
            return 'scoring'
        return 'settlement'

    def _begin_order_capture(self) -> None:
        """Reset direct order counts for one decision opportunity."""
        self._captured_order_event_count = 0
        self._captured_sku_order_line_counts = {}
        self._captured_order_line_quantity_squared_sums = {}
        self._captured_order_audits = []

    def _tracked_inventory_update(self, inventory, orders, policy=None):
        """Execute one order decision and retain its direct event counts."""
        if self._active_order_constraints is not None:
            order_frame = orders.get_dataframe()
            if order_frame.empty:
                decision_period = int(inventory.get_dataframe()["period"].iloc[0])
            else:
                decision_period = int(order_frame["order_period"].iloc[0])
            result = self._active_order_constraints.apply(
                orders,
                ConstraintContext(
                    inventory=inventory,
                    policy=policy,
                    decision_period=decision_period,
                ),
            )
            orders = result.order
            self._captured_order_audits.append(result.audit)
        order_frame = orders.get_dataframe()
        positive = order_frame['order_quantity'].fillna(0.0) > 0
        if positive.any():
            self._captured_order_event_count += 1
            sku_column = orders.sku_column
            positive_lines = order_frame.loc[
                positive,
                [sku_column, 'order_quantity'],
            ]
            for sku, quantity in positive_lines.itertuples(index=False, name=None):
                quantity = float(quantity)
                self._captured_sku_order_line_counts[sku] = (
                    self._captured_sku_order_line_counts.get(sku, 0) + 1
                )
                self._captured_order_line_quantity_squared_sums[sku] = (
                    self._captured_order_line_quantity_squared_sums.get(sku, 0.0)
                    + quantity ** 2
                )
        return self._update_inventory_primitive(inventory, orders, policy=policy)

    @staticmethod
    def _repository_commit() -> Optional[str]:
        """Resolve HEAD without invoking Git; return None outside a checkout."""
        def validated(value: str) -> Optional[str]:
            candidate = value.strip()
            if len(candidate) != 40:
                return None
            if any(character not in "0123456789abcdefABCDEF" for character in candidate):
                return None
            return candidate.lower()

        repository = Path(__file__).resolve().parents[3]
        git_dir = repository / ".git"
        head_path = git_dir / "HEAD"
        if not head_path.is_file():
            return None
        head = head_path.read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref_name = head.removeprefix("ref: ")
            ref_path = git_dir / ref_name
            if ref_path.is_file():
                return validated(ref_path.read_text(encoding="utf-8"))
            packed_refs = git_dir / "packed-refs"
            if packed_refs.is_file():
                for line in packed_refs.read_text(encoding="utf-8").splitlines():
                    if line and not line.startswith(("#", "^")):
                        commit, name = line.split(" ", 1)
                        if name == ref_name:
                            return validated(commit)
            return None
        return validated(head)

    @staticmethod
    def _build_run_manifest(
        *,
        demand_data: pd.DataFrame,
        demand_source_name: str,
        demand_source_type: str,
        random_seed: Optional[int],
        source_commit: Optional[str],
        policy: BasePolicy,
        sku_column: str,
        run_settings: dict,
        opening_inventory: dict,
    ) -> dict:
        """Build a serializable manifest for one simulation run."""
        demand_checksum = SimulationEngine._dataframe_checksum(
            demand_data,
            sort_columns=["period", sku_column],
        )
        try:
            package_version = importlib.metadata.version("pyforia")
        except importlib.metadata.PackageNotFoundError:
            package_version = None
        try:
            matplotlib_version = importlib.metadata.version("matplotlib")
        except importlib.metadata.PackageNotFoundError:
            matplotlib_version = None
        get_metadata = getattr(policy, "get_target_metadata", None)
        target_metadata = get_metadata() if callable(get_metadata) else {}
        return {
            'run_id': str(uuid4()),
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'demand_source': {
                'name': demand_source_name,
                'type': demand_source_type,
                'sha256': demand_checksum,
                'rows': len(demand_data),
                'random_seed': random_seed,
                'generation_provenance': copy.deepcopy(
                    demand_data.attrs.get("pyforia_demand_provenance")
                ),
            },
            'package': {
                'version': package_version,
                'source': {
                    'commit': source_commit,
                    'dirty': None,
                },
            },
            'policy': {
                'class': type(policy).__name__,
                'name': policy.policy_name,
                'lead_time': policy.lead_time,
                'review_period': policy.review_period,
                'service_level': policy.service_level,
                'allow_backorders': policy.allow_backorders,
                'target_metadata': target_metadata,
                'target_data': SimulationEngine._policy_target_fingerprint(policy),
            },
            'opening_inventory': copy.deepcopy(opening_inventory),
            'run_settings': copy.deepcopy(run_settings),
            'dependencies': {
                'python': platform.python_version(),
                'numpy': np.__version__,
                'pandas': pd.__version__,
                'matplotlib': matplotlib_version,
            },
        }

    @staticmethod
    def _dataframe_checksum(frame: pd.DataFrame, sort_columns: List[str]) -> str:
        """Hash a DataFrame's schema and values in a deterministic row order."""
        ordered = frame.sort_values(sort_columns, kind="stable").reset_index(drop=True)
        row_hashes = pd.util.hash_pandas_object(ordered, index=True).to_numpy()
        hasher = hashlib.sha256()
        hasher.update("|".join(map(str, ordered.columns)).encode("utf-8"))
        hasher.update("|".join(map(str, ordered.dtypes)).encode("utf-8"))
        hasher.update(row_hashes.tobytes())
        return hasher.hexdigest()

    @staticmethod
    def _inventory_fingerprint(inventory: InventoryStateDataFrame) -> dict:
        """Identify the complete opening state, including its pipeline."""
        state = inventory.get_dataframe().copy()
        state['in_transit'] = state['in_transit'].map(
            lambda value: json.dumps(np.asarray(value, dtype=float).tolist(), separators=(",", ":"))
        )
        return {
            'sha256': SimulationEngine._dataframe_checksum(
                state,
                sort_columns=[inventory.sku_column],
            ),
            'rows': len(state),
            'columns': list(state.columns),
            'sku_column': inventory.sku_column,
            'max_lead_time': inventory.max_lead_time,
            'allow_backorders': inventory.allow_backorders,
        }

    @staticmethod
    def _policy_target_fingerprint(policy: BasePolicy) -> Optional[dict]:
        """Identify the exact fitted target table without copying it into a manifest."""
        target_data = getattr(policy, "target_df_", None)
        if not isinstance(target_data, pd.DataFrame):
            target_data = getattr(policy, "target_table", None)
        if not isinstance(target_data, pd.DataFrame):
            return None
        sort_columns = [
            column
            for column in ["unique_id", "fh", "date", "period"]
            if column in target_data.columns
        ]
        if not sort_columns:
            sort_columns = list(target_data.columns)
        return {
            "sha256": SimulationEngine._dataframe_checksum(target_data, sort_columns),
            "rows": len(target_data),
            "columns": list(target_data.columns),
        }

    @staticmethod
    def _validate_policy_schedule(
        policy: BasePolicy,
        policy_schedule: Optional[Mapping[int, BasePolicy]],
        *,
        opening_period: int,
        n_periods: int,
        initial_decision: str,
        opening_date: pd.Timestamp,
        period_offset,
    ) -> Dict[int, BasePolicy]:
        """Validate explicit fitted-policy snapshots for decision dates."""
        if policy_schedule is None:
            return {}
        if not isinstance(policy_schedule, Mapping):
            raise TypeError("policy_schedule must be a mapping of decision period to policy")

        allowed_periods = {
            period
            for period in range(opening_period + 1, opening_period + n_periods + 1)
            if period % policy.review_period == 0
        }
        if initial_decision == "before_first_demand":
            allowed_periods.add(opening_period)

        validated = {}
        configuration = (
            type(policy),
            policy.lead_time,
            policy.review_period,
            policy.service_level,
            policy.allow_backorders,
        )
        for period, snapshot in policy_schedule.items():
            if not isinstance(period, int) or isinstance(period, bool):
                raise ValueError("policy_schedule keys must be integer decision periods")
            if period not in allowed_periods:
                raise ValueError(
                    f"policy_schedule period {period} is not a decision event in this run"
                )
            if not isinstance(snapshot, BasePolicy) or not snapshot.fitted_:
                raise ValueError(
                    f"policy_schedule period {period} must contain a fitted BasePolicy"
                )
            snapshot_configuration = (
                type(snapshot),
                snapshot.lead_time,
                snapshot.review_period,
                snapshot.service_level,
                snapshot.allow_backorders,
            )
            if snapshot_configuration != configuration:
                raise ValueError(
                    "policy_schedule snapshots may change fitted targets only; policy class, "
                    "lead_time, review_period, service_level, and backorder mode must match"
                )
            decision_date = opening_date + (period - opening_period) * period_offset
            SimulationEngine._validate_policy_information_origin(
                snapshot,
                latest_allowed_origin=decision_date,
                exact=True,
                expected_frequency=period_offset,
            )
            validated[period] = copy.deepcopy(snapshot)
        return validated

    @staticmethod
    def _validate_policy_information_origin(
        policy: BasePolicy,
        *,
        latest_allowed_origin: pd.Timestamp,
        exact: bool,
        expected_frequency,
    ) -> None:
        """Validate declared target origin against the simulation information date."""
        get_metadata = getattr(policy, "get_target_metadata", None)
        if not callable(get_metadata):
            return
        metadata = get_metadata()
        if "forecast_origin" not in metadata:
            raise ValueError("fitted policy target metadata must include forecast_origin")
        if "forecast_frequency" not in metadata:
            raise ValueError("fitted policy target metadata must include forecast_frequency")
        policy_frequency = _require_forward_frequency(
            metadata["forecast_frequency"],
            "policy forecast_frequency",
        )
        if policy_frequency != expected_frequency:
            raise ValueError(
                f"policy forecast_frequency {policy_frequency.freqstr} must match "
                f"simulation period_frequency {expected_frequency.freqstr}"
            )
        origin = pd.Timestamp(metadata["forecast_origin"])
        allowed = pd.Timestamp(latest_allowed_origin)
        if (exact and origin != allowed) or (not exact and origin > allowed):
            relation = "equal" if exact else "not be after"
            raise ValueError(
                f"policy forecast_origin {origin} must {relation} decision information "
                f"date {allowed}"
            )

    @staticmethod
    def _policy_for_decision(
        active_policy: BasePolicy,
        policy_schedule: Mapping[int, BasePolicy],
        period: int,
        update_log: list,
    ) -> BasePolicy:
        """Select the fitted snapshot for a decision and record its provenance."""
        if period not in policy_schedule:
            return active_policy
        snapshot = copy.deepcopy(policy_schedule[period])
        metadata = {}
        get_metadata = getattr(snapshot, "get_target_metadata", None)
        if callable(get_metadata):
            metadata = get_metadata()
        update_log.append({
            'decision_period': period,
            'policy_name': snapshot.policy_name,
            'target_metadata': metadata,
            'target_data': SimulationEngine._policy_target_fingerprint(snapshot),
        })
        return snapshot

    # ---- Demand source resolution ----

    @staticmethod
    def _materialize_demand_source(demand_source, n_periods: int) -> pd.DataFrame:
        """Materialize callable demand once so a run has one immutable input."""
        if isinstance(demand_source, pd.DataFrame):
            return demand_source.copy()
        if not callable(demand_source):
            raise TypeError("demand_source must be a pandas DataFrame or callable")

        frames = []
        provenance_rows = []
        for period in range(n_periods):
            frame = demand_source(period)
            if not isinstance(frame, pd.DataFrame):
                raise TypeError("demand_source callable must return a pandas DataFrame")
            frames.append(frame.copy())
            provenance = frame.attrs.get("pyforia_demand_provenance")
            if provenance is not None:
                provenance_rows.append(copy.deepcopy(provenance))
        if not frames:
            return pd.DataFrame(columns=['unique_id', 'period', 'date', 'y'])
        materialized = pd.concat(frames, ignore_index=True)
        if provenance_rows:
            if len(provenance_rows) != len(frames):
                raise ValueError(
                    "callable demand source supplied generation provenance for only "
                    "some periods"
                )
            modes = {row.get("negative_demand_handling") for row in provenance_rows}
            if len(modes) != 1:
                raise ValueError("callable demand source changed negative-demand handling mode")
            minima = [
                row.get("minimum_clipped_value")
                for row in provenance_rows
                if row.get("minimum_clipped_value") is not None
            ]
            materialized.attrs["pyforia_demand_provenance"] = {
                "negative_demand_handling": modes.pop(),
                "clipped_negative_count": sum(
                    int(row.get("clipped_negative_count", 0)) for row in provenance_rows
                ),
                "minimum_clipped_value": min(minima) if minima else None,
            }
        return materialized

    @staticmethod
    def _validate_demand_calendar(
        demand_data: pd.DataFrame,
        inventory: InventoryStateDataFrame,
        n_periods: int,
        period_offset,
    ) -> pd.DataFrame:
        """Validate the complete SKU-period-date grid before state mutation."""
        sku_column = inventory.sku_column
        required = [sku_column, 'period', 'date', 'y']
        missing_columns = [column for column in required if column not in demand_data.columns]
        if missing_columns:
            raise ValueError(f"demand_source is missing required columns: {missing_columns}")

        validated = demand_data.copy()
        periods = pd.to_numeric(validated['period'], errors='coerce')
        if periods.isna().any() or not np.isfinite(periods.to_numpy(dtype=float)).all():
            raise ValueError("demand_source.period must contain finite integers")
        if not np.equal(periods, np.floor(periods)).all():
            raise ValueError("demand_source.period must contain integers")
        validated['period'] = periods.astype(int)

        demand_values = pd.to_numeric(validated['y'], errors='coerce')
        if demand_values.isna().any() or not np.isfinite(demand_values.to_numpy(dtype=float)).all():
            raise ValueError("demand_source.y must contain finite values")
        if (demand_values < 0).any():
            raise ValueError("demand_source.y must be non-negative")
        validated['y'] = demand_values.astype(float)

        _require_identifiers(
            validated,
            sku_column,
            'demand_source',
            unique=False,
        )
        if validated.duplicated(['period', sku_column]).any():
            raise ValueError("demand_source contains duplicate SKU-period rows")

        expected_periods = set(range(n_periods))
        actual_periods = set(validated['period'].unique().tolist())
        if actual_periods != expected_periods:
            raise ValueError(
                f"demand_source periods must equal 0..{max(n_periods - 1, 0)}; "
                f"got {sorted(actual_periods)}"
            )

        expected_skus = _require_identifiers(
            inventory.get_dataframe(),
            sku_column,
            'inventory_state',
            unique=True,
        )
        for period in range(n_periods):
            period_skus = set(
                validated.loc[validated['period'] == period, sku_column].tolist()
            )
            if period_skus != expected_skus:
                missing = _identifier_sample(expected_skus - period_skus)
                extra = _identifier_sample(period_skus - expected_skus)
                raise ValueError(
                    f"demand_source period {period} has an incomplete SKU grid; "
                    f"missing={missing}, extra={extra}"
                )

        dates = pd.to_datetime(validated['date'], errors='coerce')
        if dates.isna().any():
            raise ValueError("demand_source.date must contain complete valid dates")
        validated['date'] = dates
        opening_dates = pd.to_datetime(inventory.get_dataframe()['date'], errors='coerce')
        if opening_dates.isna().any() or opening_dates.nunique() != 1:
            raise ValueError("inventory must contain one complete opening date")
        opening_date = opening_dates.iloc[0]
        for period in range(n_periods):
            period_dates = validated.loc[validated['period'] == period, 'date']
            expected_date = opening_date + (period + 1) * period_offset
            if period_dates.nunique() != 1 or period_dates.iloc[0] != expected_date:
                actual = sorted(str(value) for value in period_dates.unique())
                raise ValueError(
                    f"demand_source period {period} must use date {expected_date}; got {actual}"
                )
        return validated

    def _resolve_demand_source(self, demand_source):
        """Convert demand_source to callable(period) -> demand_df."""
        if 'period' not in demand_source.columns:
            raise ValueError("demand_source DataFrame must contain a 'period' column")
        # DataFrame: filter by 'period' column
        def demand_fn(period):
            return demand_source[demand_source['period'] == period]
        return demand_fn

    # ---- Overridable hooks ----

    def on_review_period(self, inventory, policy, period):
        """
        Called on review periods to place orders.

        Override to customize ordering logic (e.g., reforecasting, conditional ordering).
        Must return the updated InventoryStateDataFrame.

        Args:
            inventory: Current InventoryStateDataFrame
            policy: The fitted policy
            period: Current simulation period (0-indexed loop counter)

        Returns:
            Updated InventoryStateDataFrame with orders placed
        """
        orders = policy.predict(inventory, current_period=period)
        inventory = self._update_inventory(inventory, orders, policy=policy)
        return inventory

    def _attach_order_audit(self, event_df: pd.DataFrame) -> pd.DataFrame:
        """Attach requested-versus-feasible order quantities to event rows."""
        event_df = event_df.copy()
        if not self._captured_order_audits:
            event_df['requested_order_quantity'] = event_df['order_quantity']
            event_df['constrained_order_quantity'] = event_df['order_quantity']
            event_df['constraint_adjustment_units'] = 0.0
            event_df['constraint_binding_flag'] = False
            event_df['capacity_violation_flag'] = False
            event_df['binding_constraints'] = ""
            return event_df
        audit = pd.concat(self._captured_order_audits, ignore_index=True)
        audit = audit.groupby('unique_id', as_index=False, sort=False).agg({
            'requested_order_quantity': 'sum',
            'constrained_order_quantity': 'sum',
            'constraint_adjustment_units': 'sum',
            'constraint_binding_flag': 'any',
            'capacity_violation_flag': 'any',
            'binding_constraints': lambda values: "|".join(
                value for value in values.astype(str) if value
            ),
        })
        return event_df.merge(
            audit,
            on='unique_id',
            how='left',
            validate='one_to_one',
        )

    def on_stockout(self, inventory, period):
        """
        Called when any SKU has a stockout in the current period.

        Override for custom stockout handling (logging, alerts, etc.).

        Args:
            inventory: Current InventoryStateDataFrame (after demand processing)
            period: Current simulation period (0-indexed loop counter)
        """
        pass

    def before_step(self, inventory, period):
        """Called before each period's demand processing."""
        pass

    def before_demand(self, inventory, demand_df, period):
        """
        Called immediately before demand is processed.

        Override to update inventory state for calendar effects such as
        spoilage/expiry before the normal Pyforia demand transition runs.
        Must return an InventoryStateDataFrame.
        """
        return inventory

    def after_period_event(self, event_df, inventory, period):
        """
        Called after the normalized event frame is built for one period.

        Override to add audit columns derived from hook-managed state. The
        returned frame is appended to SimulationResult.to_event_frame().
        """
        return event_df

    # ---- Multi-policy comparison ----

    def run_comparison(
        self,
        policies: List[BasePolicy],
        demand_source: Union[pd.DataFrame, Callable],
        inventory: InventoryStateDataFrame,
        n_periods: int,
        *,
        period_frequency: str,
        initial_decision: str,
        warmup_periods: int,
        scoring_periods: int,
        settlement_periods: int,
        order_during_settlement: bool,
        demand_source_name: str,
        random_seed: Optional[int],
        labels: Optional[List[str]] = None,
        policy_schedules: Optional[List[Optional[Mapping[int, BasePolicy]]]] = None,
        order_constraints: Optional[OrderingConstraints] = None,
    ) -> 'ComparisonResult':
        """
        Run simulation for multiple policies and compare results.

        Each policy gets its own deep-copied inventory. All policies see the same demand.

        Args:
            policies: List of fitted BasePolicy instances.
            demand_source: DataFrame or callable, same for all policies.
            inventory: Initialized InventoryStateDataFrame (deep-copied per policy).
            n_periods: Number of periods to simulate.
            period_frequency: Explicit pandas frequency for one period.
            initial_decision: ``"before_first_demand"`` or ``"none"``.
            warmup_periods: State-advancing periods excluded from scoring.
            scoring_periods: Periods included in result summaries.
            settlement_periods: Tail periods excluded from scoring.
            order_during_settlement: Whether review decisions remain active in
                the settlement tail.
            demand_source_name: Non-empty experiment identifier for demand.
            random_seed: Demand/scenario seed, or explicit ``None``.
            labels: Optional display names for each policy. Defaults to policy_name.
            policy_schedules: Optional list of rolling fitted-policy schedules,
                one per policy.
            order_constraints: Optional constraints applied identically to each policy.

        Returns:
            ComparisonResult with all SimulationResults accessible by label.
        """
        if not isinstance(policies, list) or not policies:
            raise ValueError("policies must be a non-empty list")
        if labels is None:
            labels = self._deduplicate_labels([p.policy_name for p in policies])
        else:
            if not isinstance(labels, list):
                raise TypeError("labels must be a list of non-empty unique strings")
            if len(labels) != len(policies):
                raise ValueError(
                    f"labels length ({len(labels)}) != policies length ({len(policies)})"
                )
        if any(
            not isinstance(label, str)
            or not label.strip()
            or label != label.strip()
            for label in labels
        ):
            raise ValueError(
                "labels must contain non-empty strings without surrounding whitespace"
            )
        if len(labels) != len(set(labels)):
            raise ValueError("labels must be unique")
        if policy_schedules is None:
            policy_schedules = [None] * len(policies)
        elif len(policy_schedules) != len(policies):
            raise ValueError(
                f"policy_schedules length ({len(policy_schedules)}) != policies length "
                f"({len(policies)})"
            )

        self._log(f"[SimEngine] Comparing {len(policies)} policies: {labels}")

        original_demand_source_type = (
            "dataframe" if isinstance(demand_source, pd.DataFrame) else "callable"
        )
        shared_demand = self._materialize_demand_source(demand_source, n_periods)
        results = {}
        for i, (policy, label, schedule) in enumerate(
            zip(policies, labels, policy_schedules)
        ):
            self._log(f"[SimEngine] Running {i + 1}/{len(policies)}: {label}")
            inv_copy = copy.deepcopy(inventory)
            policy_copy = copy.deepcopy(policy)
            result = self.run(
                policy_copy,
                shared_demand,
                inv_copy,
                n_periods,
                period_frequency=period_frequency,
                initial_decision=initial_decision,
                warmup_periods=warmup_periods,
                scoring_periods=scoring_periods,
                settlement_periods=settlement_periods,
                order_during_settlement=order_during_settlement,
                demand_source_name=demand_source_name,
                random_seed=random_seed,
                policy_schedule=schedule,
                order_constraints=order_constraints,
            )
            result.run_manifest['demand_source']['type'] = original_demand_source_type
            result.run_manifest['demand_source']['materialized_once_for_comparison'] = True
            results[label] = result

        return ComparisonResult(results)

    @staticmethod
    def _deduplicate_labels(labels: List[str]) -> List[str]:
        """Append _1, _2, etc. to duplicate labels."""
        seen = {}
        result = []
        for label in labels:
            if label in seen:
                seen[label] += 1
                result.append(f"{label}_{seen[label]}")
            else:
                seen[label] = 0
                result.append(label)
        return result
