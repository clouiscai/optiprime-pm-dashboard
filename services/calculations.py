from datetime import date

from sqlalchemy.orm import Session, joinedload

from models.entities import Blocker, BOMItem, BudgetLog, Project, Sponsor, Task, Team


def task_with_open_blockers(task: Task) -> dict:
    child_count = len(task.children)
    displayed_progress = round(sum(child.progress for child in task.children) / child_count) if child_count else task.progress
    return {
        "id": task.id,
        "project_id": task.project_id,
        "team_id": task.team_id,
        "parent_task_id": task.parent_task_id,
        "title": task.title,
        "description": task.description,
        "owner": task.owner,
        "status": task.status,
        "priority": task.priority,
        "start_date": task.start_date,
        "due_date": task.due_date,
        "dependencies": task.dependencies or [],
        "progress": displayed_progress,
        "open_blockers": len([b for b in task.blockers if b.status == "open"]),
        "child_count": child_count,
        "is_parent": child_count > 0,
    }


def project_dashboard(db: Session, project: Project, team_id: int | None = None) -> dict:
    team = db.get(Team, team_id) if team_id else None

    teams = db.query(Team).filter(Team.project_id == project.id).order_by(Team.code).all()
    team_count = max(1, len(teams))
    shared_bom_items = db.query(BOMItem).filter(BOMItem.project_id == project.id, BOMItem.team_id.is_(None)).all()
    shared_bom_total = round(sum(item.quantity * item.unit_cost for item in shared_bom_items), 2)
    shared_bom_per_team = round(shared_bom_total / team_count, 2) if teams else 0

    all_tasks = db.query(Task).filter(Task.project_id == project.id).all()
    all_bom_items = db.query(BOMItem).filter(BOMItem.project_id == project.id).all()
    all_logs = db.query(BudgetLog).filter(BudgetLog.project_id == project.id, BudgetLog.invoice_id.is_not(None)).all()
    all_sponsors = db.query(Sponsor).filter(Sponsor.project_id == project.id).all()
    all_blockers = (
        db.query(Blocker)
        .options(joinedload(Blocker.task))
        .join(Task, Blocker.task_id == Task.id)
        .filter(Task.project_id == project.id, Blocker.status == "open")
        .all()
    )
    all_sponsor_total = round(sum(sponsor.amount for sponsor in all_sponsors), 2)
    project_planned_budget = all_sponsor_total if all_sponsor_total else project.budget
    allocated_budget = round(sum(summary_team.budget for summary_team in teams), 2)
    unallocated_budget = round(project_planned_budget - allocated_budget, 2)
    unallocated_actual_spend = round(
        sum(log.amount for log in all_logs if log.team_id is None and not log.sponsored_by),
        2,
    )
    unallocated_remaining = round(unallocated_budget - unallocated_actual_spend, 2)

    if team_id:
        tasks = [task for task in all_tasks if task.team_id == team_id]
        blockers = [blocker for blocker in all_blockers if blocker.task and blocker.task.team_id == team_id]
        bom_items = [item for item in all_bom_items if item.team_id == team_id or item.team_id is None]
        logs = [log for log in all_logs if log.team_id == team_id]
        sponsors = [sponsor for sponsor in all_sponsors if sponsor.team_id == team_id]
    else:
        tasks = all_tasks
        blockers = all_blockers
        bom_items = all_bom_items
        logs = all_logs
        sponsors = all_sponsors

    parent_ids = {child.parent_task_id for child in tasks if child.parent_task_id}
    leaf_tasks = [task for task in tasks if task.id not in parent_ids]
    progress_tasks = leaf_tasks or tasks
    done_tasks = len([t for t in progress_tasks if t.status == "done"])
    overdue_tasks = len([t for t in progress_tasks if t.due_date and t.due_date < date.today() and t.status != "done"])
    completion = round((sum(t.progress for t in progress_tasks) / len(progress_tasks)), 1) if progress_tasks else 0
    bom_total = round(sum(item.quantity * item.unit_cost for item in bom_items), 2)
    if team_id:
        own_bom_total = round(sum(item.quantity * item.unit_cost for item in bom_items if item.team_id == team_id), 2)
        bom_total = round(own_bom_total + shared_bom_per_team, 2)
    budget_log_total = round(sum(log.amount for log in logs if not log.sponsored_by), 2)
    sponsor_total = round(sum(sponsor.amount for sponsor in sponsors), 2)
    expected_spend = bom_total
    actual_spend = budget_log_total

    status_counts: dict[str, int] = {"todo": 0, "in_progress": 0, "blocked": 0, "done": 0}
    priority_counts: dict[str, int] = {}
    for task in tasks:
        if task in progress_tasks:
            status_counts[task.status] = status_counts.get(task.status, 0) + 1
            priority_counts[task.priority] = priority_counts.get(task.priority, 0) + 1
    team_summaries = []
    for summary_team in teams:
        team_tasks = [task for task in all_tasks if task.team_id == summary_team.id]
        team_bom_total = sum(item.quantity * item.unit_cost for item in all_bom_items if item.team_id == summary_team.id)
        team_bom_total += shared_bom_per_team
        team_log_total = sum(log.amount for log in all_logs if log.team_id == summary_team.id and not log.sponsored_by)
        team_sponsor_total = sum(sponsor.amount for sponsor in all_sponsors if sponsor.team_id == summary_team.id)
        team_parent_ids = {task.parent_task_id for task in team_tasks if task.parent_task_id}
        team_leaf_tasks = [task for task in team_tasks if task.id not in team_parent_ids] or team_tasks
        team_planned_budget = summary_team.budget
        team_summaries.append(
            {
                "id": summary_team.id,
                "code": summary_team.code,
                "name": summary_team.name,
                "domain": summary_team.domain,
                "budget": team_planned_budget,
                "sponsor_total": round(team_sponsor_total, 2),
                "completion": round(sum(task.progress for task in team_leaf_tasks) / len(team_leaf_tasks), 1) if team_leaf_tasks else 0,
                "open_blockers": len([blocker for blocker in all_blockers if blocker.task and blocker.task.team_id == summary_team.id]),
                "expected_spend": round(team_bom_total, 2),
                "actual_spend": round(team_log_total, 2),
            }
        )
    if team:
        planned_budget = team.budget
    else:
        planned_budget = project_planned_budget

    return {
        "project": project,
        "scope": team.code if team else "master",
        "team": team,
        "completion": completion,
        "active_blockers": len(blockers),
        "overdue_tasks": overdue_tasks,
        "total_tasks": len(progress_tasks),
        "done_tasks": done_tasks,
        "bom_total": bom_total,
        "budget_log_total": budget_log_total,
        "sponsor_total": sponsor_total,
        "planned_budget": planned_budget,
        "expected_spend": expected_spend,
        "actual_spend": actual_spend,
        "remaining_budget": round(planned_budget - actual_spend, 2),
        "unallocated_budget": unallocated_budget,
        "unallocated_actual_spend": unallocated_actual_spend,
        "unallocated_remaining": unallocated_remaining,
        "status_counts": status_counts,
        "priority_counts": priority_counts,
        "team_summaries": team_summaries,
    }
