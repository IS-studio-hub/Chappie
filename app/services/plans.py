from dataclasses import dataclass
from typing import Literal

PlanId = Literal["free", "small", "mid", "large"]


@dataclass(frozen=True)
class Plan:
    id: PlanId
    name: str
    price_cad: float
    max_searches: int
    max_results: int
    search_cost_cad: float
    # When out of credit: "upgrade" shows plan upgrade, "contact" shows IS Studio CTA
    exhausted_action: str


PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        price_cad=0,
        max_searches=1,
        max_results=10,
        search_cost_cad=0,
        exhausted_action="upgrade",
    ),
    "small": Plan(
        id="small",
        name="Small Biz",
        price_cad=29.0,
        max_searches=10,
        max_results=30,
        search_cost_cad=5.8,
        exhausted_action="upgrade",
    ),
    "mid": Plan(
        id="mid",
        name="Mid Biz",
        price_cad=49.0,
        max_searches=10,
        max_results=50,
        search_cost_cad=5.4,
        exhausted_action="upgrade",
    ),
    "large": Plan(
        id="large",
        name="Large Biz",
        price_cad=169.0,
        max_searches=30,
        max_results=50,
        search_cost_cad=7.6,
        exhausted_action="contact",
    ),
}

PLAN_ORDER = ["free", "small", "mid", "large"]


def get_plan(plan_id: str | None) -> Plan:
    return PLANS.get(plan_id or "free", PLANS["free"])


def is_upgrade(from_plan: str, to_plan: str) -> bool:
    try:
        return PLAN_ORDER.index(to_plan) > PLAN_ORDER.index(from_plan)
    except ValueError:
        return False
