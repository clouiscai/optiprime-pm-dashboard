from datetime import date

from database.session import SessionLocal, init_db
from models.entities import Blocker, BOMItem, BudgetLog, Project, Sponsor, Task, Team, User


TEAM_DEFS = [
    {
        "code": "UAV",
        "name": "UAV Team",
        "domain": "Aerial drones",
        "description": "Airborne autonomy, payload, flight controls, and ground-station integration.",
        "budget": 35000,
    },
    {
        "code": "USV",
        "name": "USV Team",
        "domain": "Surface sea drones",
        "description": "Surface vessel hull, propulsion, marine electronics, and field operations.",
        "budget": 55000,
    },
    {
        "code": "UUV",
        "name": "UUV Team",
        "domain": "Underwater drones",
        "description": "Subsurface enclosure, pressure handling, tethering, and underwater sensing.",
        "budget": 35000,
    },
]


def ensure_teams(db, project):
    teams = {team.code: team for team in db.query(Team).filter(Team.project_id == project.id).all()}
    for definition in TEAM_DEFS:
        if definition["code"] not in teams:
            team = Team(project_id=project.id, **definition)
            db.add(team)
            db.flush()
            teams[team.code] = team
    return teams


def assign_existing_unscoped_records(db, project, teams):
    team_cycle = [teams["USV"], teams["UAV"], teams["UUV"]]
    for index, log in enumerate(db.query(BudgetLog).filter(BudgetLog.project_id == project.id, BudgetLog.team_id.is_(None)).order_by(BudgetLog.id).all()):
        log.team_id = team_cycle[index % len(team_cycle)].id

    for user in db.query(User).filter(User.team_id.is_(None)).order_by(User.id).all():
        role = user.role.lower()
        if "mechanical" in role or "electrical" in role:
            user.team_id = teams["USV"].id
        elif "controls" in role:
            user.team_id = teams["UAV"].id
        else:
            user.team_id = teams["UUV"].id


def seed():
    init_db()
    db = SessionLocal()
    try:
        if db.query(Project).count():
            project = db.query(Project).order_by(Project.id).first()
            project.name = "OptiPrime Autonomous Platform"
            project.description = "Integrated UAV, USV, and UUV delivery plan for OptiPrime."
            teams = ensure_teams(db, project)
            assign_existing_unscoped_records(db, project, teams)
            if db.query(Sponsor).filter(Sponsor.project_id == project.id).count() == 0:
                db.add_all(
                    [
                        Sponsor(project_id=project.id, team_id=teams["UAV"].id, name="AeroLab Grant", amount=35000, date=date(2026, 4, 1), notes="UAV autonomy allocation"),
                        Sponsor(project_id=project.id, team_id=teams["USV"].id, name="Maritime Systems Partner", amount=55000, date=date(2026, 4, 1), notes="USV prototype allocation"),
                        Sponsor(project_id=project.id, team_id=teams["UUV"].id, name="DeepTech Fund", amount=35000, date=date(2026, 4, 1), notes="UUV pressure test allocation"),
                    ]
                )
            db.commit()
            return

        project = Project(
            name="OptiPrime Autonomous Platform",
            description="Integrated UAV, USV, and UUV delivery plan for OptiPrime.",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 8, 31),
            budget=125000,
        )
        db.add(project)
        db.flush()
        teams = ensure_teams(db, project)

        users = [
            User(name="Ari Tan", role="Master Lead", team_id=teams["USV"].id),
            User(name="Mina Koh", role="USV Mechanical", team_id=teams["USV"].id),
            User(name="Dev Rao", role="UAV Controls", team_id=teams["UAV"].id),
            User(name="Sam Lee", role="UUV Electrical", team_id=teams["UUV"].id),
        ]
        db.add_all(users)

        tasks = [
            Task(project_id=project.id, team_id=teams["USV"].id, title="Finalize USV hull CAD", owner="Mina Koh", status="done", priority="high", start_date=date(2026, 4, 1), due_date=date(2026, 4, 15), progress=100),
            Task(project_id=project.id, team_id=teams["USV"].id, title="USV motor controller bench test", owner="Sam Lee", status="in_progress", priority="high", start_date=date(2026, 4, 12), due_date=date(2026, 5, 3), dependencies=[1], progress=55),
            Task(project_id=project.id, team_id=teams["UAV"].id, title="UAV perception pipeline integration", owner="Dev Rao", status="blocked", priority="critical", start_date=date(2026, 4, 20), due_date=date(2026, 5, 18), dependencies=[2], progress=35),
            Task(project_id=project.id, team_id=teams["UUV"].id, title="UUV waterproof electronics enclosure", owner="Sam Lee", status="todo", priority="medium", start_date=date(2026, 5, 1), due_date=date(2026, 5, 24), dependencies=[1], progress=0),
            Task(project_id=project.id, team_id=teams["USV"].id, title="Master field shakedown test", owner="Ari Tan", status="todo", priority="high", start_date=date(2026, 6, 4), due_date=date(2026, 6, 8), dependencies=[2, 3, 4], progress=0),
        ]
        db.add_all(tasks)
        db.flush()

        db.add(Blocker(task_id=tasks[2].id, description="Camera driver latency exceeds target on Windows test rig.", severity="high", status="open"))

        db.add_all(
            [
                BOMItem(project_id=project.id, team_id=teams["USV"].id, name="Marine-grade BLDC motor", quantity=4, unit_cost=2100),
                BOMItem(project_id=project.id, team_id=teams["UAV"].id, name="Stereo camera module", quantity=2, unit_cost=1850),
                BOMItem(project_id=project.id, team_id=teams["UAV"].id, name="Embedded compute unit", quantity=1, unit_cost=4200),
                BOMItem(project_id=project.id, team_id=teams["USV"].id, name="Carbon fiber hull panels", quantity=6, unit_cost=950),
                BOMItem(project_id=project.id, team_id=teams["UUV"].id, name="Depth-rated connector set", quantity=8, unit_cost=260),
            ]
        )

        db.add_all(
            [
                BudgetLog(project_id=project.id, team_id=teams["USV"].id, category="Prototype machining", amount=7300, date=date(2026, 4, 8), notes="Hull revision A"),
                BudgetLog(project_id=project.id, team_id=teams["USV"].id, category="Test facility", amount=1800, date=date(2026, 4, 22), notes="Wave tank reservation"),
                BudgetLog(project_id=project.id, team_id=teams["UUV"].id, category="Pressure test rig", amount=2400, date=date(2026, 4, 27), notes="UUV dry pressure validation"),
            ]
        )
        db.add_all(
            [
                Sponsor(project_id=project.id, team_id=teams["UAV"].id, name="AeroLab Grant", amount=35000, date=date(2026, 4, 1), notes="UAV autonomy allocation"),
                Sponsor(project_id=project.id, team_id=teams["USV"].id, name="Maritime Systems Partner", amount=55000, date=date(2026, 4, 1), notes="USV prototype allocation"),
                Sponsor(project_id=project.id, team_id=teams["UUV"].id, name="DeepTech Fund", amount=35000, date=date(2026, 4, 1), notes="UUV pressure test allocation"),
            ]
        )
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
