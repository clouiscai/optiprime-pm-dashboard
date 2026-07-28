from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from models.entities import BudgetLog


ADJUSTMENT_CATEGORIES = {"tax", "taxes", "gst", "vat", "discount", "discounts"}


def is_adjustment_category(category: str) -> bool:
    normalized = category.strip().lower().replace("-", " ").replace("_", " ")
    words = set(normalized.split())
    return bool(words & ADJUSTMENT_CATEGORIES)


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
