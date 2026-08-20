from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from models.entities import BudgetLog


TAX_CATEGORIES = {"tax", "taxes", "gst", "vat"}
DISCOUNT_CATEGORIES = {"discount", "discounts", "rebate", "rebates"}
ADJUSTMENT_CATEGORIES = TAX_CATEGORIES | DISCOUNT_CATEGORIES
SERVICE_CATEGORIES = {
    "service",
    "services",
    "labour",
    "labor",
    "shipping",
    "freight",
    "fee",
    "fees",
    "duty",
    "handling",
}
INVENTORY_CATEGORIES = ("Equipment", "Tools", "Hardware", "Consumables", "Utilities", "Unsorted")


def is_adjustment_category(category: str) -> bool:
    normalized = category.strip().lower().replace("-", " ").replace("_", " ")
    words = set(normalized.split())
    return bool(words & ADJUSTMENT_CATEGORIES)


def is_discount_category(category: str) -> bool:
    normalized = category.strip().lower().replace("-", " ").replace("_", " ")
    return bool(set(normalized.split()) & DISCOUNT_CATEGORIES)


def spending_classification(log: BudgetLog) -> tuple[str, str | None]:
    normalized = (log.category or "").strip().lower().replace("-", " ").replace("_", " ")
    words = set(normalized.split())
    if "enablement" in words:
        return "Team Enablement", None
    if words & SERVICE_CATEGORIES or words & ADJUSTMENT_CATEGORIES:
        return "Services", None

    inventory_category = (log.inventory_category or "Unsorted").strip() or "Unsorted"
    if inventory_category == "Assets":
        inventory_category = "Hardware"
    if inventory_category not in INVENTORY_CATEGORIES:
        inventory_category = "Unsorted"
    return "Items", inventory_category


def spending_classification_weights(
    log: BudgetLog,
    logs_by_id: dict[int, BudgetLog],
    team_id: int | None = None,
) -> dict[tuple[str, str | None], Decimal]:
    targets = [
        logs_by_id[target_id]
        for target_id in log.referenced_item_ids
        if target_id in logs_by_id and logs_by_id[target_id].invoice_id == log.invoice_id
    ]
    if not is_adjustment_category(log.category) or not targets:
        return {spending_classification(log): Decimal("1")}

    target_amounts = [abs(Decimal(str(target.amount or 0))) for target in targets]
    total_target_amount = sum(target_amounts, Decimal("0"))
    if total_target_amount == 0:
        target_amounts = [Decimal("1") for _ in targets]
    if team_id is not None:
        target_amounts = [
            target_amount * direct_allocation_weights(target).get(team_id, Decimal("0"))
            for target, target_amount in zip(targets, target_amounts)
        ]
    total_target_amount = sum(target_amounts, Decimal("0"))
    if total_target_amount == 0:
        return {spending_classification(log): Decimal("1")}

    weights: dict[tuple[str, str | None], Decimal] = {}
    for target, target_amount in zip(targets, target_amounts):
        classification = spending_classification(target)
        weights[classification] = weights.get(classification, Decimal("0")) + (
            target_amount / total_target_amount
        )
    return weights


def direct_allocation_weights(log: BudgetLog) -> dict[int | None, Decimal]:
    team_ids = sorted(set(log.team_ids or ([log.team_id] if log.team_id else [])))
    if not team_ids:
        return {None: Decimal("1")}
    share = Decimal("1") / Decimal(len(team_ids))
    return {team_id: share for team_id in team_ids}


def purchase_allocation_weights(
    log: BudgetLog,
    logs_by_id: dict[int, BudgetLog],
) -> dict[int | None, Decimal]:
    targets = [
        logs_by_id[target_id]
        for target_id in log.referenced_item_ids
        if target_id in logs_by_id and logs_by_id[target_id].invoice_id == log.invoice_id
    ]
    if not targets:
        return direct_allocation_weights(log)

    target_amounts = [abs(Decimal(str(target.amount or 0))) for target in targets]
    total_target_amount = sum(target_amounts, Decimal("0"))
    if total_target_amount == 0:
        target_amounts = [Decimal("1") for _ in targets]
        total_target_amount = Decimal(len(targets))

    weights: dict[int | None, Decimal] = {}
    for target, target_amount in zip(targets, target_amounts):
        target_weight = target_amount / total_target_amount
        for team_id, team_weight in direct_allocation_weights(target).items():
            weights[team_id] = weights.get(team_id, Decimal("0")) + (target_weight * team_weight)
    return weights or direct_allocation_weights(log)


def split_amount_by_weights(
    amount: float,
    weights: dict[int | None, Decimal],
) -> dict[int | None, float]:
    total_cents = int(
        (Decimal(str(amount)) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    if not weights:
        return {None: total_cents / 100}

    total_weight = sum(weights.values(), Decimal("0"))
    if total_weight <= 0:
        return {None: total_cents / 100}

    sign = -1 if total_cents < 0 else 1
    absolute_cents = abs(total_cents)
    raw_shares = {
        team_id: Decimal(absolute_cents) * weight / total_weight
        for team_id, weight in weights.items()
    }
    cents = {
        team_id: int(raw.to_integral_value(rounding=ROUND_FLOOR))
        for team_id, raw in raw_shares.items()
    }
    remainder = absolute_cents - sum(cents.values())
    ranked = sorted(
        raw_shares,
        key=lambda team_id: (
            -(raw_shares[team_id] - Decimal(cents[team_id])),
            -1 if team_id is None else team_id,
        ),
    )
    for index in range(remainder):
        cents[ranked[index % len(ranked)]] += 1
    return {team_id: sign * value / 100 for team_id, value in cents.items()}


def project_purchase_allocations(logs: list[BudgetLog]) -> dict[int, dict[int | None, float]]:
    logs_by_id = {log.id: log for log in logs}
    return {
        log.id: split_amount_by_weights(log.amount, purchase_allocation_weights(log, logs_by_id))
        for log in logs
    }


def project_spending_type_summaries(
    logs: list[BudgetLog],
    purchase_allocations: dict[int, dict[int | None, float]],
    team_id: int | None = None,
) -> list[dict]:
    logs_by_id = {log.id: log for log in logs}
    totals: dict[tuple[str, str | None], float] = {}
    for log in logs:
        if log.sponsored_by:
            continue
        scoped_amount = log.amount if team_id is None else purchase_allocations.get(log.id, {}).get(team_id, 0)
        for classification, amount in split_amount_by_weights(
            scoped_amount,
            spending_classification_weights(log, logs_by_id, team_id),
        ).items():
            totals[classification] = totals.get(classification, 0) + amount

    summaries = []
    for spending_type in ("Items", "Services", "Team Enablement"):
        categories = [
            {"category": category, "amount": round(totals.get((spending_type, category), 0), 2)}
            for category in INVENTORY_CATEGORIES
            if round(totals.get((spending_type, category), 0), 2) != 0
        ] if spending_type == "Items" else []
        amount = round(
            sum(value for (group, _), value in totals.items() if group == spending_type),
            2,
        )
        if amount != 0 or categories:
            summaries.append({"type": spending_type, "amount": amount, "categories": categories})
    return summaries
