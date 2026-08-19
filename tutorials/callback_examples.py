"""Executable built-in and custom callback examples for the staging tree."""

import pandas as pd

from pyforia import (
    InventoryAdjustmentResult,
    InventoryStateDataFrame,
    ScheduledOrderMultiplier,
    SimulationCallback,
    SimulationEngine,
)
from pyforia.core.base_policy import BasePolicy
from pyforia.core.data_structures import OrderDecision


class ExampleFixedOrderPolicy(BasePolicy):
    """Small explicit policy used only to make this example executable."""

    def __init__(self):
        super().__init__(
            lead_time=1,
            review_period=1,
            service_level=0.95,
            allow_backorders=False,
        )
        self.fitted_ = True

    def fit(self, forecast_df=None, **kwargs):
        return self

    def predict(self, inventory_state_df, current_period=0, **kwargs):
        frame = inventory_state_df.inventory_position()
        frame["order_quantity"] = 4.0
        frame["target_level"] = frame["inventory_position"] + 4.0
        frame["reorder_point"] = pd.NA
        frame["order_period"] = current_period
        frame["expected_delivery_period"] = current_period + self.lead_time
        return OrderDecision(
            frame,
            lead_time=self.lead_time,
            review_period=self.review_period,
        )


class SecondWeekLoss(SimulationCallback):
    """Remove a configured fraction of post-demand on-hand in days 8--14."""

    def __init__(self, fraction=0.10):
        self.fraction = float(fraction)

    def on_after_demand(self, context):
        if not 8 <= context.date.day <= 14:
            return None
        state = context.inventory
        return InventoryAdjustmentResult(pd.DataFrame({
            "unique_id": state[context.sku_column],
            "quantity_delta": -self.fraction * state["on_hand"],
            "reason": "second-week handling loss",
            "source": "callback tutorial",
        }))

    def get_config(self):
        return {"fraction": self.fraction}


def run_examples():
    """Run one built-in and one custom callback scenario."""
    inventory = InventoryStateDataFrame(["A"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.data["on_hand"] = 10.0
    demand = pd.DataFrame({
        "unique_id": ["A"] * 8,
        "period": list(range(8)),
        "date": pd.date_range("2025-01-02", periods=8, freq="D"),
        "y": [1.0] * 8,
    })
    multiplier = ScheduledOrderMultiplier(pd.DataFrame({
        "unique_id": ["A"],
        "period": [1],
        "multiplier": [2.0],
        "reason": ["planned promotion"],
        "source": ["callback tutorial"],
    }))
    common = {
        "policy": ExampleFixedOrderPolicy(),
        "demand_source": demand,
        "inventory": inventory,
        "n_periods": 8,
        "period_frequency": "D",
        "initial_decision": "none",
        "warmup_periods": 0,
        "scoring_periods": 8,
        "settlement_periods": 0,
        "order_during_settlement": False,
        "demand_source_name": "callback_tutorial",
        "random_seed": None,
    }
    built_in_result = SimulationEngine().run(**common, callbacks=[multiplier])
    custom_result = SimulationEngine().run(
        **common,
        callbacks=[SecondWeekLoss(0.10)],
    )
    return built_in_result, custom_result


if __name__ == "__main__":
    built_in, custom = run_examples()
    print(built_in.to_callback_audit_frame())
    print(custom.to_callback_audit_frame())
