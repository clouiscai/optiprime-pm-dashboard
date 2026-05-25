import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import optiPrimeLogo from "./assets/OptiPrime_logo_blackbg.jpg";
import { REALTIME_ENABLED, apiFetch, downloadCsv, getApiBase, getWsUrl } from "./api";

const tabs = ["Dashboard", "Tasks", "Kanban", "Gantt", "BOM/Budget", "Equipments/Asset", "Members", "Sponsors", "Blockers"];
const statusColumns = [
  ["todo", "To Do"],
  ["in_progress", "In Progress"],
  ["blocked", "Blocked"],
  ["done", "Done"],
];
const teamColors = {
  UAV: "#b8c0cc",
  USV: "#ff3b3b",
  UUV: "#1f2329",
  General: "#64748b",
};

const today = new Date().toISOString().slice(0, 10);
const dayMs = 24 * 60 * 60 * 1000;

function emptyTask(projectId, teamId) {
  return {
    project_id: projectId,
    team_id: teamId || null,
    parent_task_id: null,
    title: "",
    description: "",
    owner: "",
    status: "todo",
    priority: "medium",
    start_date: today,
    due_date: today,
    dependencies: [],
    progress: 0,
  };
}

function parseDay(value) {
  const [year, month, day] = value.split("-").map(Number);
  return Date.UTC(year, month - 1, day);
}

function dateFromUtc(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

function shortDate(value) {
  if (!value) return "-";
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function money(value) {
  const amount = new Intl.NumberFormat("en-SG", { maximumFractionDigits: 0 }).format(value || 0);
  return `SGD ${amount}`;
}

function priorityClass(value) {
  return `pill priority-${value || "medium"}`;
}

function severityClass(value) {
  return `pill severity-${value || "medium"}`;
}

function teamQuery(teamId) {
  return teamId === "master" ? "" : `?team_id=${teamId}`;
}

function taskTeamQuery(teamId, taskScope = "scope") {
  if (teamId === "master") return taskScope === "general" ? "?general=true" : "";
  return `?team_id=${teamId}`;
}

function teamCode(teams, id) {
  return id ? teams.find((team) => team.id === id)?.code || "Team" : "General";
}

function teamName(teams, id) {
  return id ? teams.find((team) => team.id === id)?.code || "" : "General";
}

function teamColor(teams, id) {
  const code = teamCode(teams, id);
  return teamColors[code] || teamColors.General;
}

function teamStyle(teams, id) {
  return { "--team-color": teamColor(teams, id) };
}

function wbsNumber(task, tasks) {
  const siblings = tasks
    .filter((item) => (item.parent_task_id || null) === (task.parent_task_id || null))
    .sort((a, b) => a.id - b.id);
  const index = siblings.findIndex((item) => item.id === task.id) + 1;
  if (!task.parent_task_id) return `${Math.max(1, index)}`;
  const parent = tasks.find((item) => item.id === task.parent_task_id);
  return `${parent ? wbsNumber(parent, tasks) : task.parent_task_id}.${Math.max(1, index)}`;
}

function sortWbs(tasks) {
  const byParent = new Map();
  tasks.forEach((task) => {
    const key = task.parent_task_id || 0;
    byParent.set(key, [...(byParent.get(key) || []), task]);
  });
  const result = [];
  function visit(parentId) {
    (byParent.get(parentId) || []).sort((a, b) => a.id - b.id).forEach((task) => {
      result.push(task);
      visit(task.id);
    });
  }
  visit(0);
  return result;
}

function isDescendantTask(candidate, parentId, tasks) {
  let cursor = candidate;
  const seen = new Set();
  while (cursor?.parent_task_id) {
    if (cursor.parent_task_id === parentId) return true;
    if (seen.has(cursor.parent_task_id)) return true;
    seen.add(cursor.parent_task_id);
    cursor = tasks.find((task) => task.id === cursor.parent_task_id);
  }
  return false;
}

function LoadingSkunk({ label = "Loading" }) {
  return (
    <div className="skunk-loader" role="status" aria-live="polite">
      <div className="skunk-runway">
        <img src={optiPrimeLogo} alt="" />
      </div>
      <span>{label}</span>
    </div>
  );
}

export default function App() {
  const [token, setToken] = useState(localStorage.getItem("optiprime_token") || "");
  const [role, setRole] = useState(localStorage.getItem("optiprime_role") || "admin");
  const [username, setUsername] = useState("OptiPrime");
  const [password, setPassword] = useState("");
  const [authorized, setAuthorized] = useState(false);
  const [authError, setAuthError] = useState("");
  const [activeTab, setActiveTab] = useState("Dashboard");
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(null);
  const [selectedTeam, setSelectedTeam] = useState("master");
  const [data, setData] = useState({ dashboard: null, tasks: [], blockers: [], bom: [], budget: [], invoices: [], sponsors: [], assets: [], users: [], teams: [] });
  const [busy, setBusy] = useState(false);
  const [loadingSection, setLoadingSection] = useState("");
  const [toast, setToast] = useState("");
  const [timelineStart, setTimelineStart] = useState("");
  const [timelineEnd, setTimelineEnd] = useState("");

  const currentProject = projects.find((project) => project.id === Number(projectId));
  const scopedTeamId = selectedTeam === "master" ? null : Number(selectedTeam);
  const canEdit = role === "admin";

  const resetTimelineRange = useCallback(() => {
    if (!currentProject) return;
    setTimelineStart(currentProject.start_date || today);
    setTimelineEnd(currentProject.end_date || today);
  }, [currentProject]);

  const refresh = useCallback(async (scope = activeTab) => {
    if (!projectId || !authorized) return;
    const normalizedScope = scope === "all" ? activeTab : scope;
    setBusy(true);
    setLoadingSection(normalizedScope);
    try {
      const query = teamQuery(selectedTeam);
      const requests = {
        teams: apiFetch(`/projects/${projectId}/teams`, token),
        dashboard: apiFetch(`/projects/${projectId}/dashboard${query}`, token),
      };
      if (["Dashboard", "Tasks", "Kanban", "Gantt", "Blockers", "tasks"].includes(normalizedScope)) {
        requests.tasks = apiFetch(`/projects/${projectId}/tasks${query}`, token);
      }
      if (["Dashboard", "Tasks", "Blockers", "tasks"].includes(normalizedScope)) {
        requests.blockers = apiFetch(`/projects/${projectId}/blockers${query}`, token);
      }
      if (["Tasks", "Members"].includes(normalizedScope)) {
        requests.users = apiFetch(`/users?project_id=${projectId}${selectedTeam === "master" ? "" : `&team_id=${selectedTeam}`}`, token);
      }
      if (normalizedScope === "BOM/Budget") {
        requests.bom = apiFetch(`/projects/${projectId}/bom${query}`, token);
        requests.budget = apiFetch(`/projects/${projectId}/budget${query}`, token);
        requests.invoices = apiFetch(`/projects/${projectId}/invoices`, token);
      }
      if (normalizedScope === "Equipments/Asset") {
        requests.assets = apiFetch(`/projects/${projectId}/assets${query}`, token);
      }
      if (normalizedScope === "Sponsors") {
        requests.sponsors = apiFetch(`/projects/${projectId}/sponsors`, token);
        requests.bom = apiFetch(`/projects/${projectId}/bom${query}`, token);
        requests.budget = apiFetch(`/projects/${projectId}/budget${query}`, token);
        requests.assets = apiFetch(`/projects/${projectId}/assets${query}`, token);
      }

      const entries = await Promise.all(Object.entries(requests).map(async ([key, request]) => [key, await request]));
      const updates = Object.fromEntries(entries);
      setData((current) => ({
        ...current,
        teams: Array.isArray(updates.teams) ? updates.teams : current.teams,
        dashboard: updates.dashboard || current.dashboard,
        tasks: Array.isArray(updates.tasks) ? updates.tasks : current.tasks,
        blockers: Array.isArray(updates.blockers) ? updates.blockers : current.blockers,
        bom: Array.isArray(updates.bom) ? updates.bom : current.bom,
        budget: Array.isArray(updates.budget) ? updates.budget : current.budget,
        invoices: Array.isArray(updates.invoices) ? updates.invoices : current.invoices,
        sponsors: Array.isArray(updates.sponsors) ? updates.sponsors : current.sponsors,
        assets: Array.isArray(updates.assets) ? updates.assets : current.assets,
        users: Array.isArray(updates.users) ? updates.users : current.users,
      }));
    } catch (error) {
      setToast(error.message);
    } finally {
      setBusy(false);
      setLoadingSection("");
    }
  }, [activeTab, authorized, projectId, selectedTeam, token]);

  async function loadWorkspace(nextToken, nextRole = "admin") {
    const loadedProjects = await apiFetch("/projects", nextToken);
    setProjects(loadedProjects);
    setProjectId(null);
    setSelectedTeam("master");
    setActiveTab("Dashboard");
    setToken(nextToken);
    setRole(nextRole);
    localStorage.setItem("optiprime_token", nextToken);
    localStorage.setItem("optiprime_role", nextRole);
    setAuthorized(true);
  }

  async function login(event) {
    event.preventDefault();
    setAuthError("");
    try {
      const response = await fetch(`${getApiBase()}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) throw new Error("Invalid username or password");
      const payload = await response.json();
      await loadWorkspace(payload.token, payload.role);
    } catch {
      setAuthError("Invalid username or password.");
    }
  }

  useEffect(() => {
    if (!token) return;
    apiFetch("/auth/verify", token)
      .then((payload) => loadWorkspace(token, payload.role))
      .catch(() => {
        localStorage.removeItem("optiprime_token");
        localStorage.removeItem("optiprime_role");
        setToken("");
      });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    resetTimelineRange();
  }, [resetTimelineRange]);

  useEffect(() => {
    if (!authorized || !REALTIME_ENABLED) return;
    const socket = new WebSocket(getWsUrl(token));
    socket.onmessage = () => refresh();
    socket.onerror = () => setToast("Realtime connection interrupted");
    return () => socket.close();
  }, [authorized, refresh, token]);

  async function patchTask(taskId, patch) {
    if (!canEdit) {
      setToast("This account is view-only.");
      return;
    }
    await apiFetch(`/tasks/${taskId}`, token, { method: "PATCH", body: JSON.stringify(patch) });
    await refresh("tasks");
  }

  async function deleteTask(task) {
    if (!canEdit) {
      setToast("This account is view-only.");
      return;
    }
    if (!window.confirm(`Delete task "${task.title}"? This also removes its blockers and audit history.`)) return;
    await apiFetch(`/tasks/${task.id}`, token, { method: "DELETE" });
    await refresh("tasks");
  }

  function logout() {
    localStorage.removeItem("optiprime_token");
    localStorage.removeItem("optiprime_role");
    setToken("");
    setRole("admin");
    setAuthorized(false);
    setPassword("");
  }

  async function reloadProjects() {
    setBusy(true);
    setLoadingSection("Projects");
    try {
      const loadedProjects = await apiFetch("/projects", token);
      setProjects(Array.isArray(loadedProjects) ? loadedProjects : []);
      return loadedProjects;
    } finally {
      setBusy(false);
      setLoadingSection("");
    }
  }

  function openProject(id) {
    setProjectId(id);
    setSelectedTeam("master");
    setActiveTab("Dashboard");
    setData({ dashboard: null, tasks: [], blockers: [], bom: [], budget: [], invoices: [], sponsors: [], assets: [], users: [], teams: [] });
  }

  function closeProject() {
    setProjectId(null);
    setSelectedTeam("master");
    setActiveTab("Dashboard");
  }

  if (!authorized) {
    return (
      <main className="login-shell">
        <form className="login-panel" onSubmit={login}>
          <img className="login-logo" src={optiPrimeLogo} alt="OptiPrime" />
          <h1>OptiPrime</h1>
          <label>
            Username
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoFocus />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          {authError && <p className="error">{authError}</p>}
          <button type="submit">Sign In</button>
        </form>
      </main>
    );
  }

  if (!projectId) {
    return (
      <ProjectPortal
        projects={projects}
        token={token}
        canEdit={canEdit}
        onProjectsChange={setProjects}
        onReloadProjects={reloadProjects}
        onOpenProject={openProject}
        onLogout={logout}
        loading={busy && loadingSection === "Projects"}
      />
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-logo" src={optiPrimeLogo} alt="OptiPrime" />
          <div>
            <strong>OptiPrime</strong>
            <span>{canEdit ? "Admin" : "View only"}</span>
          </div>
        </div>
        <button className="project-exit-button" onClick={closeProject}>Projects</button>
        <div className="team-switcher">
          <button className={selectedTeam === "master" ? "active" : ""} onClick={() => setSelectedTeam("master")}>Master</button>
          {data.teams.map((team) => (
            <button className={String(selectedTeam) === String(team.id) ? "active" : ""} key={team.id} onClick={() => setSelectedTeam(team.id)}>
              {team.code}
            </button>
          ))}
        </div>
        <nav>
          {tabs.map((tab) => (
            <button className={activeTab === tab ? "active" : ""} key={tab} onClick={() => setActiveTab(tab)}>
              {tab}
            </button>
          ))}
        </nav>
        <button className="signout-button" onClick={logout}>Log Out</button>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <p>{selectedTeam === "master" ? currentProject?.description : data.dashboard?.team?.description}</p>
            <h1>{activeTab}</h1>
          </div>
          <div className="topbar-meta">
            <label>
              From
              <input type="date" value={timelineStart} onChange={(event) => setTimelineStart(event.target.value)} />
            </label>
            <label>
              To
              <input type="date" value={timelineEnd} onChange={(event) => setTimelineEnd(event.target.value)} />
            </label>
          </div>
        </header>

        {toast && (
          <button className="toast" onClick={() => setToast("")}>
            {toast}
          </button>
        )}

        <div className="section-loading-region" aria-busy={busy}>
          {busy && loadingSection && <LoadingSkunk label={`Loading ${loadingSection}`} />}
          {activeTab === "Dashboard" && (
            <Dashboard
              dashboard={data.dashboard}
              tasks={data.tasks}
              teams={data.teams}
              projectId={projectId}
              token={token}
              selectedTeam={selectedTeam}
              canEdit={canEdit}
              onRefresh={refresh}
            />
          )}
          {activeTab === "Tasks" && (
            <TasksView
              projectId={projectId}
              teamId={scopedTeamId}
              selectedTeam={selectedTeam}
              token={token}
              tasks={data.tasks}
              teams={data.teams}
              users={data.users}
              canEdit={canEdit}
              onRefresh={refresh}
              onPatchTask={patchTask}
              onDeleteTask={deleteTask}
            />
          )}
          {activeTab === "Kanban" && <Kanban tasks={data.tasks} teams={data.teams} canEdit={canEdit} onPatchTask={patchTask} />}
          {activeTab === "Gantt" && <Gantt tasks={data.tasks} teams={data.teams} rangeStart={timelineStart} rangeEnd={timelineEnd} onResetRange={resetTimelineRange} />}
          {activeTab === "BOM/Budget" && (
            <BomBudget
              projectId={projectId}
              teamId={scopedTeamId}
              selectedTeam={selectedTeam}
              token={token}
              teams={data.teams}
              dashboard={data.dashboard}
              bom={data.bom}
              budget={data.budget}
              invoices={data.invoices}
              canEdit={canEdit}
              onRefresh={refresh}
            />
          )}
          {activeTab === "Equipments/Asset" && (
            <Assets
              projectId={projectId}
              teamId={scopedTeamId}
              selectedTeam={selectedTeam}
              token={token}
              teams={data.teams}
              assets={data.assets}
              canEdit={canEdit}
              onRefresh={refresh}
            />
          )}
          {activeTab === "Members" && (
            <Members
              teamId={scopedTeamId}
              selectedTeam={selectedTeam}
              token={token}
              teams={data.teams}
              users={data.users}
              canEdit={canEdit}
              onRefresh={refresh}
            />
          )}
          {activeTab === "Sponsors" && (
            <Sponsors
              projectId={projectId}
              teamId={scopedTeamId}
              selectedTeam={selectedTeam}
              token={token}
              teams={data.teams}
              sponsors={data.sponsors}
              bom={data.bom}
              budget={data.budget}
              assets={data.assets}
              dashboard={data.dashboard}
              canEdit={canEdit}
              onRefresh={refresh}
            />
          )}
          {activeTab === "Blockers" && (
            <Blockers token={token} blockers={data.blockers} tasks={data.tasks} canEdit={canEdit} onRefresh={refresh} />
          )}
        </div>
      </main>
    </div>
  );
}

function Dashboard({ dashboard, tasks, teams, projectId, token, selectedTeam, canEdit, onRefresh }) {
  if (!dashboard) return null;
  const overdue = tasks.filter((task) => task.due_date && task.due_date < today && task.status !== "done");
  return (
    <section className="stack">
      <div className="metrics-grid">
        <Metric label="Completion" value={`${dashboard.completion}%`} detail={`${dashboard.done_tasks}/${dashboard.total_tasks} tasks done`} />
        <Metric label="Active blockers" value={dashboard.active_blockers} detail="Open engineering constraints" tone={dashboard.active_blockers ? "warn" : ""} />
        <Metric label="Budget used" value={money(dashboard.actual_spend)} detail={`${money(dashboard.remaining_budget)} remaining`} />
        <Metric label="Overdue" value={dashboard.overdue_tasks} detail="Tasks past due" tone={dashboard.overdue_tasks ? "danger" : ""} />
      </div>
      {selectedTeam === "master" && (
        <div className="team-rollup">
          {dashboard.team_summaries.map((team) => (
            <article key={team.id}>
              <div>
                <strong>{team.code}</strong>
                <span>{team.domain}</span>
              </div>
              <b>{team.completion}%</b>
              <span>{money(team.actual_spend)} used</span>
              <i>{team.open_blockers} blockers</i>
            </article>
          ))}
        </div>
      )}
      {selectedTeam === "master" && (
        <TeamSetup projectId={projectId} token={token} teams={teams} canEdit={canEdit} onRefresh={onRefresh} />
      )}
      <div className="dashboard-band">
        <div>
          <h2>Flow</h2>
          <div className="bars">
            {statusColumns.map(([key, label]) => (
              <div className="bar-row" key={key}>
                <span>{label}</span>
                <div>
                  <i style={{ width: `${Math.max(6, (dashboard.status_counts[key] / Math.max(1, dashboard.total_tasks)) * 100)}%` }} />
                </div>
                <b>{dashboard.status_counts[key] || 0}</b>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h2>Attention</h2>
          <div className="attention-list">
            {overdue.length === 0 && <p>No overdue tasks.</p>}
            {overdue.map((task) => (
              <article key={task.id}>
                <strong>{task.title}</strong>
                <span>{task.owner || "Unassigned"} - due {shortDate(task.due_date)}</span>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function Metric({ label, value, detail, tone = "" }) {
  return (
    <article className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
    </article>
  );
}

function MetricButton({ label, value, detail, tone = "", onClick }) {
  return (
    <button className={`metric metric-button ${tone}`} onClick={onClick}>
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
    </button>
  );
}

function TasksView({ projectId, teamId, selectedTeam, token, tasks, teams, users, canEdit, onRefresh, onPatchTask, onDeleteTask }) {
  const isMaster = selectedTeam === "master";
  const defaultTeam = isMaster ? null : teamId || teams[0]?.id || null;
  const [draft, setDraft] = useState(emptyTask(projectId, defaultTeam));
  const [filter, setFilter] = useState("all");
  const [editingTaskId, setEditingTaskId] = useState(null);
  const [taskEdit, setTaskEdit] = useState({ title: "", description: "" });
  const wbsTasks = useMemo(() => sortWbs(tasks), [tasks]);
  const filtered = filter === "all" ? wbsTasks : wbsTasks.filter((task) => task.priority === filter || task.status === filter);
  const draftTeamId = draft.team_id ? Number(draft.team_id) : null;
  const parentOptions = wbsTasks.filter((task) => (task.team_id || null) === draftTeamId && task.parent_task_id !== task.id);
  const draftMembers = draftTeamId ? users.filter((user) => user.team_id === draftTeamId) : users;

  useEffect(() => {
    setDraft(emptyTask(projectId, defaultTeam));
  }, [projectId, defaultTeam]);

  async function createTask(event) {
    event.preventDefault();
    if (!draft.title.trim()) return;
    await apiFetch("/tasks", token, { method: "POST", body: JSON.stringify({ ...draft, project_id: projectId, team_id: draft.team_id ? Number(draft.team_id) : null }) });
    setDraft(emptyTask(projectId, defaultTeam));
    await onRefresh("tasks");
  }

  function editTask(task) {
    setEditingTaskId(task.id);
    setTaskEdit({ title: task.title, description: task.description || "", start_date: task.start_date || today, due_date: task.due_date || today });
  }

  async function saveTask(task) {
    if (!taskEdit.title.trim()) return;
    await onPatchTask(task.id, { title: taskEdit.title, description: taskEdit.description, start_date: taskEdit.start_date, due_date: taskEdit.due_date });
    setEditingTaskId(null);
  }

  return (
    <section className="stack">
      {canEdit && <form className="task-composer task-composer-wide" onSubmit={createTask}>
        <label className="compact-field">
          <span>Task</span>
          <input placeholder="New task" value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
        </label>
        {isMaster ? (
          <label className="compact-field">
            <span>Team</span>
            <select value={draft.team_id || ""} onChange={(event) => setDraft({ ...draft, team_id: event.target.value ? Number(event.target.value) : null, parent_task_id: null })}>
              <option value="">General</option>
              {teams.map((team) => (
                <option value={team.id} key={team.id}>{team.code}</option>
              ))}
            </select>
          </label>
        ) : (
          <label className="compact-field">
            <span>Team</span>
            <div className="locked-field">{teamName(teams, defaultTeam)}</div>
          </label>
        )}
        <label className="compact-field">
          <span>Owner</span>
          <select value={draft.owner} onChange={(event) => setDraft({ ...draft, owner: event.target.value })}>
            <option value="">Owner</option>
            {draftMembers.map((user) => (
              <option key={user.id}>{user.name}</option>
            ))}
          </select>
        </label>
        <label className="compact-field">
          <span>Parent</span>
          <select value={draft.parent_task_id || ""} onChange={(event) => setDraft({ ...draft, parent_task_id: event.target.value ? Number(event.target.value) : null })}>
            <option value="">No parent</option>
            {parentOptions.map((task) => (
              <option value={task.id} key={task.id}>{teamCode(teams, task.team_id)} {wbsNumber(task, wbsTasks)} - {task.title}</option>
            ))}
          </select>
        </label>
        <label className="compact-field">
          <span>Priority</span>
          <select value={draft.priority} onChange={(event) => setDraft({ ...draft, priority: event.target.value })}>
            <option>low</option>
            <option>medium</option>
            <option>high</option>
            <option>critical</option>
          </select>
        </label>
        <label className="compact-field">
          <span>Start</span>
          <input type="date" value={draft.start_date} onChange={(event) => setDraft({ ...draft, start_date: event.target.value })} />
        </label>
        <label className="compact-field">
          <span>End</span>
          <input type="date" value={draft.due_date} onChange={(event) => setDraft({ ...draft, due_date: event.target.value })} />
        </label>
        <button type="submit">Add Task</button>
      </form>}

      <div className="toolbar">
        <select value={filter} onChange={(event) => setFilter(event.target.value)}>
          <option value="all">All tasks</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="blocked">Blocked</option>
          <option value="done">Done</option>
        </select>
        <button onClick={() => downloadCsv(`/projects/${projectId}/tasks/export.csv${taskTeamQuery(selectedTeam)}`, token, "robotx-tasks.csv")}>Export CSV</button>
      </div>

      <div className="table-wrap">
        <table className="task-table">
          <thead>
            <tr>
              <th>WBS</th>
              {isMaster && <th>Team</th>}
              <th>Task</th>
              <th>Parent</th>
              <th>Owner</th>
              <th>Status</th>
              <th>Priority</th>
              <th>Dates</th>
              <th>Progress</th>
              {canEdit && <th></th>}
            </tr>
          </thead>
          <tbody>
            {filtered.map((task) => {
              const isEditing = editingTaskId === task.id;
              return (
                <tr key={task.id} style={teamStyle(teams, task.team_id)} className={`${task.open_blockers ? "blocked-row" : ""} ${task.is_parent ? "wbs-parent-row" : ""} ${task.parent_task_id ? "wbs-child-row" : ""}`}>
                  <td>
                    <span className="wbs-code">{wbsNumber(task, wbsTasks)}</span>
                    <small>{task.is_parent ? "Parent" : task.parent_task_id ? "Subtask" : "Task"}</small>
                  </td>
                  {isMaster && (
                    <td>
                      <select value={task.team_id || ""} disabled={!canEdit} onChange={(event) => onPatchTask(task.id, { team_id: event.target.value ? Number(event.target.value) : null })}>
                        <option value="">General</option>
                        {teams.map((team) => (
                          <option value={team.id} key={team.id}>{team.code}</option>
                        ))}
                      </select>
                    </td>
                  )}
                  <td className={task.parent_task_id ? "wbs-child-title" : ""}>
                    {isEditing ? (
                      <div className="task-edit-fields">
                        <input value={taskEdit.title} onChange={(event) => setTaskEdit({ ...taskEdit, title: event.target.value })} />
                        <textarea value={taskEdit.description} onChange={(event) => setTaskEdit({ ...taskEdit, description: event.target.value })} />
                      </div>
                    ) : (
                      <>
                        <strong>{task.title}</strong>
                        <p>{task.description || "No description"}</p>
                      </>
                    )}
                  </td>
                  <td>
                    <select value={task.parent_task_id || ""} disabled={!canEdit} onChange={(event) => onPatchTask(task.id, { parent_task_id: event.target.value ? Number(event.target.value) : null })}>
                      <option value="">None</option>
                      {wbsTasks
                        .filter((candidate) => candidate.id !== task.id && (candidate.team_id || null) === (task.team_id || null) && !isDescendantTask(candidate, task.id, wbsTasks))
                        .map((candidate) => (
                          <option value={candidate.id} key={candidate.id}>{wbsNumber(candidate, wbsTasks)} {candidate.title}</option>
                        ))}
                    </select>
                  </td>
                  <td>
                    <select value={task.owner || ""} disabled={!canEdit} onChange={(event) => onPatchTask(task.id, { owner: event.target.value })}>
                      <option value="">Unassigned</option>
                      {users
                        .filter((user) => task.team_id ? user.team_id === task.team_id : true)
                        .map((user) => (
                          <option key={user.id}>{user.name}</option>
                        ))}
                    </select>
                  </td>
                  <td>
                    <select value={task.status} disabled={!canEdit} onChange={(event) => onPatchTask(task.id, { status: event.target.value })}>
                      {statusColumns.map(([key, label]) => (
                        <option value={key} key={key}>{label}</option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select value={task.priority} disabled={!canEdit} onChange={(event) => onPatchTask(task.id, { priority: event.target.value })}>
                      <option>low</option>
                      <option>medium</option>
                      <option>high</option>
                      <option>critical</option>
                    </select>
                  </td>
                  <td>
                    {isEditing ? (
                      <div className="date-edit-fields">
                        <input type="date" value={taskEdit.start_date} onChange={(event) => setTaskEdit({ ...taskEdit, start_date: event.target.value })} />
                        <input type="date" value={taskEdit.due_date} onChange={(event) => setTaskEdit({ ...taskEdit, due_date: event.target.value })} />
                      </div>
                    ) : (
                      <>{shortDate(task.start_date)} to {shortDate(task.due_date)}</>
                    )}
                  </td>
                  <td>
                    <input className="progress-slider" type="range" min="0" max="100" step="5" value={task.progress} style={{ "--progress": `${task.progress}%` }} disabled={task.is_parent || !canEdit} title={task.is_parent ? "Parent progress is averaged from subtasks" : "Task progress"} onChange={(event) => onPatchTask(task.id, { progress: Number(event.target.value) })} />
                    <span>{task.progress}%{task.is_parent ? " avg" : ""}</span>
                  </td>
                  {canEdit && (
                    <td className="row-actions">
                      {isEditing ? (
                        <>
                          <button onClick={() => saveTask(task)}>Save</button>
                          <button onClick={() => setEditingTaskId(null)}>Cancel</button>
                        </>
                      ) : (
                        <button onClick={() => editTask(task)}>Edit</button>
                      )}
                      <button className="danger-button" onClick={() => onDeleteTask(task)}>Delete</button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Kanban({ tasks, teams, canEdit, onPatchTask }) {
  const [draggedId, setDraggedId] = useState(null);
  return (
    <section className="kanban">
      {statusColumns.map(([status, label]) => (
        <div className="kanban-column" key={status} onDragOver={(event) => canEdit && event.preventDefault()} onDrop={() => canEdit && draggedId && onPatchTask(draggedId, { status })}>
          <header>
            <h2>{label}</h2>
            <span>{tasks.filter((task) => task.status === status).length}</span>
          </header>
          {tasks
            .filter((task) => task.status === status)
            .map((task) => (
              <article className={`task-card ${task.open_blockers ? "has-blocker" : ""}`} style={teamStyle(teams, task.team_id)} draggable={canEdit} key={task.id} onDragStart={() => canEdit && setDraggedId(task.id)}>
                <strong>{task.title}</strong>
                <span>{teamCode(teams, task.team_id)} - {task.owner || "Unassigned"}</span>
                <div className="card-meta">
                  <i className={priorityClass(task.priority)}>{task.priority}</i>
                  <b>{task.progress}%</b>
                </div>
                <progress max="100" value={task.progress} />
              </article>
            ))}
        </div>
      ))}
    </section>
  );
}

function Gantt({ tasks, teams, rangeStart, rangeEnd, onResetRange }) {
  const scrollRef = useRef(null);
  const pinchRef = useRef(null);
  const [dayWidth, setDayWidth] = useState(34);
  const [detailTask, setDetailTask] = useState(null);
  const [parentsOnly, setParentsOnly] = useState(false);
  const visibleTasks = parentsOnly ? tasks.filter((task) => !task.parent_task_id) : tasks;
  const dated = sortWbs(visibleTasks).filter((task) => task.start_date && task.due_date);
  const taskById = useMemo(() => new Map(tasks.map((task) => [task.id, task])), [tasks]);
  const range = useMemo(() => {
    if (!dated.length) return null;
    const taskStarts = dated.map((task) => parseDay(task.start_date));
    const taskEnds = dated.map((task) => parseDay(task.due_date));
    const fallbackStart = Math.min(...taskStarts) - 2 * dayMs;
    const fallbackEnd = Math.max(...taskEnds) + 2 * dayMs;
    const selectedStart = rangeStart ? parseDay(rangeStart) : fallbackStart;
    const selectedEnd = rangeEnd ? parseDay(rangeEnd) : fallbackEnd;
    const start = Math.min(selectedStart, selectedEnd);
    const end = Math.max(selectedStart, selectedEnd);
    return { start, end, days: Math.round((end - start) / dayMs) + 1 };
  }, [dated, rangeStart, rangeEnd]);

  if (!dated.length || !range) return <p>No dated tasks yet.</p>;

  const timelineWidth = Math.max(760, range.days * dayWidth);
  const ticks = [];
  for (let index = 0; index < range.days; index += 7) {
    ticks.push({ left: index * dayWidth, label: shortDate(dateFromUtc(range.start + index * dayMs)) });
  }
  const todayLeft = ((parseDay(today) - range.start) / dayMs) * dayWidth;
  const showToday = todayLeft >= 0 && todayLeft <= timelineWidth;

  function leftFor(dateValue) {
    return Math.round((parseDay(dateValue) - range.start) / dayMs) * dayWidth;
  }

  function reset() {
    onResetRange();
    setDayWidth(34);
    window.requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({ left: 0, behavior: "smooth" });
    });
  }

  function zoomTimeline(event) {
    event.preventDefault();
    const direction = event.deltaY < 0 ? 1 : -1;
    setDayWidth((current) => Math.max(14, Math.min(96, current + direction * 4)));
  }

  function touchDistance(touches) {
    const [first, second] = touches;
    return Math.hypot(first.clientX - second.clientX, first.clientY - second.clientY);
  }

  function startPinchZoom(event) {
    if (event.touches.length !== 2) return;
    pinchRef.current = { distance: touchDistance(event.touches), dayWidth };
  }

  function pinchZoomTimeline(event) {
    if (event.touches.length !== 2 || !pinchRef.current) return;
    event.preventDefault();
    const nextScale = touchDistance(event.touches) / Math.max(1, pinchRef.current.distance);
    setDayWidth(Math.max(14, Math.min(96, Math.round(pinchRef.current.dayWidth * nextScale))));
  }

  function endPinchZoom(event) {
    if (event.touches.length < 2) pinchRef.current = null;
  }

  return (
    <section className="gantt-shell">
      <div className="gantt-controls">
        <label className="toggle-control">
          <input type="checkbox" checked={parentsOnly} onChange={(event) => setParentsOnly(event.target.checked)} />
          <span>Hide Subtasks</span>
        </label>
        <button onClick={reset}>Reset</button>
      </div>
      <div className={`gantt ${parentsOnly ? "gantt-parents-only" : ""}`}>
        <div className="gantt-task-column">
          <div className="gantt-task-head">Task</div>
          {dated.map((task) => (
            <button className={`gantt-task-label ${task.open_blockers ? "has-blocker" : ""} ${task.is_parent ? "wbs-parent-label" : ""} ${task.parent_task_id ? "wbs-child-label" : ""}`} style={teamStyle(teams, task.team_id)} key={task.id} onClick={() => setDetailTask(task)} type="button">
              <strong>{task.title}</strong>
              <span>{teamCode(teams, task.team_id)} {wbsNumber(task, tasks)} - {shortDate(task.start_date)} to {shortDate(task.due_date)}</span>
            </button>
          ))}
        </div>
        <div className="gantt-timeline-viewport" ref={scrollRef} onWheel={zoomTimeline} onTouchStart={startPinchZoom} onTouchMove={pinchZoomTimeline} onTouchEnd={endPinchZoom} onTouchCancel={endPinchZoom}>
          <div className="gantt-timeline-canvas" style={{ width: timelineWidth }}>
            {showToday && <i className="today-full-line" style={{ left: todayLeft }} />}
            <div className="gantt-head">
              {ticks.map((tick) => (
                <span key={tick.left} style={{ left: tick.left }}>{tick.label}</span>
              ))}
              {showToday && (
                <span className="today-label" style={{ left: todayLeft }}>
                  Today
                </span>
              )}
            </div>
            {dated.map((task) => {
              const left = leftFor(task.start_date);
              const durationDays = Math.max(1, Math.round((parseDay(task.due_date) - parseDay(task.start_date)) / dayMs) + 1);
              const width = Math.max(dayWidth, durationDays * dayWidth);
              return (
                <div className={`gantt-track ${task.open_blockers ? "has-blocker" : ""}`} style={teamStyle(teams, task.team_id)} key={task.id}>
                  {ticks.map((tick) => <i className="gantt-gridline" key={tick.left} style={{ left: tick.left }} />)}
                  {(task.dependencies || []).map((dep) => {
                    const depTask = taskById.get(dep);
                    if (!depTask?.due_date) return null;
                    return (
                      <span className="dependency-pin" style={{ left: leftFor(depTask.due_date) }} key={dep}>
                        dep {dep}
                      </span>
                    );
                  })}
                  <div className={`gantt-bar status-${task.status}`} style={{ left, width, ...teamStyle(teams, task.team_id) }} title={`${task.title}: ${task.progress}%`}>
                    {task.progress}%
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
      {detailTask && (
        <div className="modal-backdrop" onClick={() => setDetailTask(null)}>
          <article className="task-detail-modal" onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <span>{teamCode(teams, detailTask.team_id)} {wbsNumber(detailTask, tasks)}</span>
                <h2>{detailTask.title}</h2>
              </div>
              <button onClick={() => setDetailTask(null)}>Close</button>
            </header>
            <dl>
              <div><dt>Owner</dt><dd>{detailTask.owner || "Unassigned"}</dd></div>
              <div><dt>Status</dt><dd>{detailTask.status.replace("_", " ")}</dd></div>
              <div><dt>Priority</dt><dd>{detailTask.priority}</dd></div>
              <div><dt>Dates</dt><dd>{shortDate(detailTask.start_date)} to {shortDate(detailTask.due_date)}</dd></div>
              <div><dt>Progress</dt><dd>{detailTask.progress}%{detailTask.is_parent ? " average from subtasks" : ""}</dd></div>
              <div><dt>Dependencies</dt><dd>{(detailTask.dependencies || []).join(", ") || "None"}</dd></div>
            </dl>
            <p>{detailTask.description || "No description added."}</p>
          </article>
        </div>
      )}
    </section>
  );
}

function TeamSetup({ projectId, token, teams, canEdit, onRefresh }) {
  const [expanded, setExpanded] = useState(false);
  const [teamEdits, setTeamEdits] = useState({});
  const [teamDraft, setTeamDraft] = useState({
    code: "",
    name: "",
    domain: "",
    description: "",
    budget: 0,
  });

  useEffect(() => {
    const nextTeams = {};
    teams.forEach((team) => {
      nextTeams[team.id] = {
        code: team.code || "",
        name: team.name || "",
        domain: team.domain || "",
        description: team.description || "",
        budget: team.budget || 0,
      };
    });
    setTeamEdits(nextTeams);
  }, [teams]);

  async function createTeam(event) {
    event.preventDefault();
    if (!canEdit || !projectId || !teamDraft.code.trim() || !teamDraft.name.trim()) return;
    await apiFetch("/teams", token, {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        code: teamDraft.code.trim(),
        name: teamDraft.name.trim(),
        domain: teamDraft.domain.trim() || teamDraft.name.trim(),
        description: teamDraft.description.trim(),
        budget: Number(teamDraft.budget || 0),
      }),
    });
    setTeamDraft({ code: "", name: "", domain: "", description: "", budget: 0 });
    await onRefresh("Dashboard");
  }

  async function saveTeam(team) {
    if (!canEdit) return;
    const edit = teamEdits[team.id];
    await apiFetch(`/teams/${team.id}`, token, {
      method: "PATCH",
      body: JSON.stringify({
        code: edit.code.trim(),
        name: edit.name.trim(),
        domain: edit.domain.trim(),
        description: edit.description.trim(),
        budget: Number(edit.budget || 0),
      }),
    });
    await onRefresh("Dashboard");
  }

  async function deleteTeam(team) {
    if (!canEdit) return;
    if (!window.confirm(`Remove team "${team.code}"? Existing records will become General records.`)) return;
    await apiFetch(`/teams/${team.id}`, token, { method: "DELETE" });
    await onRefresh("Dashboard");
  }

  return (
    <div className="section-card team-setup-card">
      <div className="section-head">
        <h2>Teams</h2>
        {canEdit && <button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? "Done" : "Edit"}</button>}
      </div>
      {expanded && canEdit && (
        <form className="team-form" onSubmit={createTeam}>
          <label className="compact-field">
            <span>Code</span>
            <input placeholder="UAV" value={teamDraft.code} onChange={(event) => setTeamDraft({ ...teamDraft, code: event.target.value })} />
          </label>
          <label className="compact-field">
            <span>Name</span>
            <input placeholder="Team name" value={teamDraft.name} onChange={(event) => setTeamDraft({ ...teamDraft, name: event.target.value })} />
          </label>
          <label className="compact-field">
            <span>Domain</span>
            <input placeholder="Domain" value={teamDraft.domain} onChange={(event) => setTeamDraft({ ...teamDraft, domain: event.target.value })} />
          </label>
          <label className="compact-field">
            <span>Budget</span>
            <input type="number" min="0" step="0.01" value={teamDraft.budget} onChange={(event) => setTeamDraft({ ...teamDraft, budget: event.target.value })} />
          </label>
          <label className="compact-field">
            <span>Description</span>
            <input placeholder="Description" value={teamDraft.description} onChange={(event) => setTeamDraft({ ...teamDraft, description: event.target.value })} />
          </label>
          <button>Add Team</button>
        </form>
      )}
      <div className="table-wrap">
        <table className="settings-table">
          <thead>
            <tr><th>Code</th><th>Name</th><th>Domain</th><th>Budget</th><th>Description</th>{expanded && <th></th>}</tr>
          </thead>
          <tbody>
            {teams.map((team) => {
              const edit = teamEdits[team.id] || team;
              return (
                <tr key={team.id}>
                  <td>{expanded ? <input value={edit.code || ""} onChange={(event) => setTeamEdits({ ...teamEdits, [team.id]: { ...edit, code: event.target.value } })} /> : team.code}</td>
                  <td>{expanded ? <input value={edit.name || ""} onChange={(event) => setTeamEdits({ ...teamEdits, [team.id]: { ...edit, name: event.target.value } })} /> : team.name}</td>
                  <td>{expanded ? <input value={edit.domain || ""} onChange={(event) => setTeamEdits({ ...teamEdits, [team.id]: { ...edit, domain: event.target.value } })} /> : team.domain}</td>
                  <td>{expanded ? <input type="number" min="0" step="0.01" value={edit.budget || 0} onChange={(event) => setTeamEdits({ ...teamEdits, [team.id]: { ...edit, budget: event.target.value } })} /> : money(team.budget)}</td>
                  <td>{expanded ? <input value={edit.description || ""} onChange={(event) => setTeamEdits({ ...teamEdits, [team.id]: { ...edit, description: event.target.value } })} /> : team.description}</td>
                  {expanded && (
                    <td className="row-actions">
                      <button type="button" onClick={() => saveTeam(team)}>Save</button>
                      <button type="button" className="danger-button" onClick={() => deleteTeam(team)}>Remove</button>
                    </td>
                  )}
                </tr>
              );
            })}
            {teams.length === 0 && <tr><td colSpan={expanded ? "6" : "5"}>No teams yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ProjectPortal({ projects, token, canEdit, onProjectsChange, onReloadProjects, onOpenProject, onLogout, loading }) {
  const [editing, setEditing] = useState(false);
  const [projectEdits, setProjectEdits] = useState({});
  const [deleteProjectId, setDeleteProjectId] = useState(null);
  const [deleteDraft, setDeleteDraft] = useState({ admin_password: "", confirm_password: "" });
  const [projectDraft, setProjectDraft] = useState({
    name: "",
    description: "",
    start_date: today,
    end_date: today,
    budget: 0,
  });

  useEffect(() => {
    const nextProjects = {};
    projects.forEach((project) => {
      nextProjects[project.id] = {
        name: project.name || "",
        description: project.description || "",
        start_date: project.start_date || today,
        end_date: project.end_date || today,
        budget: project.budget || 0,
      };
    });
    setProjectEdits(nextProjects);
  }, [projects]);

  async function reloadProjects() {
    const loadedProjects = await onReloadProjects();
    onProjectsChange(Array.isArray(loadedProjects) ? loadedProjects : []);
    return loadedProjects;
  }

  async function createProject(event) {
    event.preventDefault();
    if (!canEdit || !projectDraft.name.trim()) return;
    const created = await apiFetch("/projects", token, {
      method: "POST",
      body: JSON.stringify({
        ...projectDraft,
        name: projectDraft.name.trim(),
        description: projectDraft.description.trim(),
        budget: Number(projectDraft.budget || 0),
      }),
    });
    const loadedProjects = await reloadProjects();
    onOpenProject(created?.id || loadedProjects?.at(-1)?.id || null);
    setProjectDraft({ name: "", description: "", start_date: today, end_date: today, budget: 0 });
  }

  async function saveProject(project) {
    if (!canEdit) return;
    const edit = projectEdits[project.id];
    await apiFetch(`/projects/${project.id}`, token, {
      method: "PATCH",
      body: JSON.stringify({
        name: edit.name.trim(),
        description: edit.description.trim(),
        start_date: edit.start_date || null,
        end_date: edit.end_date || null,
        budget: Number(edit.budget || 0),
      }),
    });
    await reloadProjects();
  }

  async function deleteProject(event, project) {
    event.preventDefault();
    if (!canEdit || !project) return;
    if (deleteDraft.admin_password !== deleteDraft.confirm_password) {
      window.alert("Admin passwords do not match.");
      return;
    }
    if (!window.confirm(`Permanently delete "${project.name}" and all of its project data?`)) return;
    await apiFetch(`/projects/${project.id}`, token, {
      method: "DELETE",
      body: JSON.stringify(deleteDraft),
    });
    setDeleteProjectId(null);
    setDeleteDraft({ admin_password: "", confirm_password: "" });
    await reloadProjects();
  }

  return (
    <main className="project-portal">
      <header className="project-portal-head">
        <div className="brand">
          <img className="brand-logo" src={optiPrimeLogo} alt="OptiPrime" />
          <div>
            <strong>OptiPrime</strong>
            <span>Project selection</span>
          </div>
        </div>
        <button className="signout-button" onClick={onLogout}>Log Out</button>
      </header>
      <section className="stack">
        {loading && <LoadingSkunk label="Loading Projects" />}
        <div className="section-card">
        <div className="section-head">
          <h1>Projects</h1>
          {canEdit && <button type="button" onClick={() => setEditing(!editing)}>{editing ? "Done" : "Edit"}</button>}
        </div>
        {editing && canEdit && (
          <form className="project-form" onSubmit={createProject}>
            <label className="compact-field">
              <span>Name</span>
              <input placeholder="Project name" value={projectDraft.name} onChange={(event) => setProjectDraft({ ...projectDraft, name: event.target.value })} />
            </label>
            <label className="compact-field">
              <span>Description</span>
              <input placeholder="Description" value={projectDraft.description} onChange={(event) => setProjectDraft({ ...projectDraft, description: event.target.value })} />
            </label>
            <label className="compact-field">
              <span>Start</span>
              <input type="date" value={projectDraft.start_date} onChange={(event) => setProjectDraft({ ...projectDraft, start_date: event.target.value })} />
            </label>
            <label className="compact-field">
              <span>End</span>
              <input type="date" value={projectDraft.end_date} onChange={(event) => setProjectDraft({ ...projectDraft, end_date: event.target.value })} />
            </label>
            <label className="compact-field">
              <span>Budget</span>
              <input type="number" min="0" step="0.01" value={projectDraft.budget} onChange={(event) => setProjectDraft({ ...projectDraft, budget: event.target.value })} />
            </label>
            <button>Add Project</button>
          </form>
        )}
        <div className="table-wrap">
          <table className="settings-table">
            <thead>
              <tr><th>Name</th><th>Description</th><th>Start</th><th>End</th><th>Budget</th><th></th></tr>
            </thead>
            <tbody>
              {projects.map((project) => {
                const edit = projectEdits[project.id] || project;
                return (
                  <Fragment key={project.id}>
                  <tr>
                    <td>{editing ? <input value={edit.name || ""} onChange={(event) => setProjectEdits({ ...projectEdits, [project.id]: { ...edit, name: event.target.value } })} /> : project.name}</td>
                    <td>{editing ? <input value={edit.description || ""} onChange={(event) => setProjectEdits({ ...projectEdits, [project.id]: { ...edit, description: event.target.value } })} /> : project.description}</td>
                    <td>{editing ? <input type="date" value={edit.start_date || ""} onChange={(event) => setProjectEdits({ ...projectEdits, [project.id]: { ...edit, start_date: event.target.value } })} /> : shortDate(project.start_date)}</td>
                    <td>{editing ? <input type="date" value={edit.end_date || ""} onChange={(event) => setProjectEdits({ ...projectEdits, [project.id]: { ...edit, end_date: event.target.value } })} /> : shortDate(project.end_date)}</td>
                    <td>{editing ? <input type="number" min="0" step="0.01" value={edit.budget || 0} onChange={(event) => setProjectEdits({ ...projectEdits, [project.id]: { ...edit, budget: event.target.value } })} /> : money(project.budget)}</td>
                    <td className="row-actions">
                      <button type="button" onClick={() => onOpenProject(project.id)}>Open</button>
                      {editing && canEdit && <button type="button" onClick={() => saveProject(project)}>Save</button>}
                      {editing && canEdit && <button type="button" className="danger-button" onClick={() => {
                        setDeleteProjectId(deleteProjectId === project.id ? null : project.id);
                        setDeleteDraft({ admin_password: "", confirm_password: "" });
                      }}>Delete</button>}
                    </td>
                  </tr>
                  {editing && deleteProjectId === project.id && (
                    <tr className="delete-confirm-row">
                      <td colSpan="6">
                        <form className="delete-project-form" onSubmit={(event) => deleteProject(event, project)}>
                          <label className="compact-field">
                            <span>Admin password</span>
                            <input type="password" value={deleteDraft.admin_password} onChange={(event) => setDeleteDraft({ ...deleteDraft, admin_password: event.target.value })} />
                          </label>
                          <label className="compact-field">
                            <span>Confirm password</span>
                            <input type="password" value={deleteDraft.confirm_password} onChange={(event) => setDeleteDraft({ ...deleteDraft, confirm_password: event.target.value })} />
                          </label>
                          <button className="danger-button">Confirm Delete</button>
                          <button type="button" onClick={() => setDeleteProjectId(null)}>Cancel</button>
                        </form>
                      </td>
                    </tr>
                  )}
                  </Fragment>
                );
              })}
              {projects.length === 0 && <tr><td colSpan="6">No projects yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      </section>
    </main>
  );
}

function BomBudget({ projectId, teamId, selectedTeam, token, teams, dashboard, bom, budget, invoices, canEdit, onRefresh }) {
  const isMaster = selectedTeam === "master";
  const defaultTeam = teamId || teams[0]?.id || null;
  const defaultBomTeam = isMaster ? null : defaultTeam;
  const emptyBomDraft = { project_id: projectId, team_id: defaultBomTeam, category: "", name: "", quantity: 1, unit_cost: 0, sponsored_by: "" };
  const emptyBudgetDraft = { project_id: projectId, team_id: defaultTeam, category: "", amount: 0, date: today, notes: "", sponsored_by: "" };
  const [bomDraft, setBomDraft] = useState(emptyBomDraft);
  const [budgetDraft, setBudgetDraft] = useState(emptyBudgetDraft);
  const [history, setHistory] = useState({});
  const [showPlan, setShowPlan] = useState(false);
  const [bomExpanded, setBomExpanded] = useState(false);
  const [budgetTab, setBudgetTab] = useState("logs");
  const [invoiceDraft, setInvoiceDraft] = useState({ description: "", file: null });
  const bomCategories = [...new Set(bom.map((item) => item.category?.trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  const groupedBom = bom.reduce((groups, item) => {
    const category = item.category?.trim() || "Uncategorized";
    if (!groups[category]) groups[category] = [];
    groups[category].push(item);
    return groups;
  }, {});
  const groupedBomEntries = Object.entries(groupedBom).sort(([a], [b]) => {
    if (a === "Uncategorized") return 1;
    if (b === "Uncategorized") return -1;
    return a.localeCompare(b);
  });

  useEffect(() => {
    setBomDraft({ project_id: projectId, team_id: defaultBomTeam, category: "", name: "", quantity: 1, unit_cost: 0, sponsored_by: "" });
    setBudgetDraft({ project_id: projectId, team_id: defaultTeam, category: "", amount: 0, date: today, notes: "", sponsored_by: "" });
    setInvoiceDraft({ description: "", file: null });
    setHistory({});
  }, [projectId, defaultTeam, defaultBomTeam, selectedTeam]);

  async function addBom(event) {
    event.preventDefault();
    if (!bomDraft.name.trim()) return;
    await apiFetch("/bom", token, {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        team_id: bomDraft.team_id ? Number(bomDraft.team_id) : null,
        category: bomDraft.category.trim(),
        name: bomDraft.name.trim(),
        quantity: Number(bomDraft.quantity),
        unit_cost: Number(bomDraft.unit_cost),
        sponsored_by: bomDraft.sponsored_by.trim(),
      }),
    });
    setBomDraft({ project_id: projectId, team_id: defaultBomTeam, category: bomDraft.category.trim(), name: "", quantity: 1, unit_cost: 0, sponsored_by: "" });
    await onRefresh();
  }

  async function addBudget(event) {
    event.preventDefault();
    if (!budgetDraft.category.trim() || !budgetDraft.team_id) return;
    await apiFetch("/budget", token, {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        team_id: Number(budgetDraft.team_id),
        category: budgetDraft.category.trim(),
        amount: Number(budgetDraft.amount),
        date: budgetDraft.date,
        notes: budgetDraft.notes.trim(),
        sponsored_by: budgetDraft.sponsored_by.trim(),
      }),
    });
    setBudgetDraft({ project_id: projectId, team_id: defaultTeam, category: "", amount: 0, date: today, notes: "", sponsored_by: "" });
    await onRefresh();
  }

  async function patchBudget(logId, patch) {
    await apiFetch(`/budget/${logId}`, token, { method: "PATCH", body: JSON.stringify(patch) });
    await onRefresh();
  }

  async function deleteBudget(log) {
    if (!window.confirm(`Delete budget log "${log.category}"?`)) return;
    await apiFetch(`/budget/${log.id}`, token, { method: "DELETE" });
    await onRefresh();
  }

  async function uploadInvoice(event) {
    event.preventDefault();
    if (!invoiceDraft.description.trim() || !invoiceDraft.file) return;
    if (invoiceDraft.file.type !== "application/pdf" || !invoiceDraft.file.name.toLowerCase().endsWith(".pdf")) {
      window.alert("Only PDF invoices are accepted.");
      return;
    }
    const form = new FormData();
    form.append("project_id", String(projectId));
    form.append("description", invoiceDraft.description.trim());
    form.append("file", invoiceDraft.file);
    try {
      const response = await fetch(`${getApiBase()}/invoices`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });
      if (!response.ok) throw new Error(await response.text() || "Invoice upload failed");
      setInvoiceDraft({ description: "", file: null });
      event.target.reset();
      await onRefresh();
    } catch (error) {
      window.alert(error.message || "Invoice upload failed");
    }
  }

  async function viewInvoice(invoice) {
    const viewer = window.open("", "_blank");
    const response = await fetch(`${getApiBase()}/invoices/${invoice.id}/file`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      viewer?.close();
      const message = await response.text();
      window.alert(message || "Invoice could not be opened.");
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    if (viewer) {
      viewer.opener = null;
      viewer.location = url;
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
    window.setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  async function deleteInvoice(invoice) {
    if (!window.confirm(`Delete invoice "${invoice.description}"?`)) return;
    await apiFetch(`/invoices/${invoice.id}`, token, { method: "DELETE" });
    await onRefresh();
  }

  async function loadHistory(itemId) {
    const versions = await apiFetch(`/bom/${itemId}/versions`, token);
    setHistory({ ...history, [itemId]: versions });
  }

  function closeHistory(itemId) {
    const next = { ...history };
    delete next[itemId];
    setHistory(next);
  }

  return (
    <section className="stack">
      <div className="metrics-grid">
        <MetricButton label="Planned" value={money(dashboard?.planned_budget)} detail="Open team allocation" onClick={() => setShowPlan(true)} />
        <Metric label="BOM" value={money(dashboard?.bom_total)} detail="Current bill of materials" />
        <Metric label="Budget logs" value={money(dashboard?.budget_log_total)} detail="Other actual spend" />
        <Metric label="Remaining" value={money(dashboard?.remaining_budget)} detail="Planned minus actual" tone={dashboard?.remaining_budget < 0 ? "danger" : ""} />
      </div>

      <div className={`split budget-split ${bomExpanded ? "bom-expanded" : ""}`}>
        <div>
          <div className="section-head">
            <h2>BOM</h2>
            <div className="section-actions">
              <button type="button" onClick={() => setBomExpanded(!bomExpanded)}>{bomExpanded ? "Collapse" : "Expand and edit"}</button>
              <button onClick={() => downloadCsv(`/projects/${projectId}/bom/export.csv${teamQuery(selectedTeam)}`, token, "robotx-bom.csv")}>Export CSV</button>
            </div>
          </div>
          {canEdit && bomExpanded && <form className="inline-form bom-form" onSubmit={addBom}>
            <datalist id="bom-category-options">
              {bomCategories.map((category) => <option value={category} key={category} />)}
            </datalist>
            {isMaster ? (
              <label className="compact-field">
                <span>Team</span>
                <select value={bomDraft.team_id || ""} onChange={(event) => setBomDraft({ ...bomDraft, team_id: event.target.value ? Number(event.target.value) : null })}>
                  <option value="">General</option>
                  {teams.map((team) => <option value={team.id} key={team.id}>{team.code}</option>)}
                </select>
              </label>
            ) : (
              <label className="compact-field">
                <span>Team</span>
                <div className="locked-field">{teamName(teams, defaultTeam)}</div>
              </label>
            )}
            <label className="compact-field bom-category-field">
              <span>Category</span>
              <input list="bom-category-options" placeholder="Category" value={bomDraft.category} onChange={(event) => setBomDraft({ ...bomDraft, category: event.target.value })} />
            </label>
            <label className="compact-field">
              <span>Item</span>
              <input placeholder="Item" value={bomDraft.name} onChange={(event) => setBomDraft({ ...bomDraft, name: event.target.value })} />
            </label>
            <label className="compact-field">
              <span>Quantity</span>
              <input type="number" min="0" step="1" value={bomDraft.quantity} onChange={(event) => setBomDraft({ ...bomDraft, quantity: event.target.value })} />
            </label>
            <label className="compact-field">
              <span>Unit Cost</span>
              <input type="number" min="0" step="0.01" value={bomDraft.unit_cost} onChange={(event) => setBomDraft({ ...bomDraft, unit_cost: event.target.value })} />
            </label>
            <label className="compact-field bom-sponsor-field">
              <span>Sponsor</span>
              <input placeholder="Optional" value={bomDraft.sponsored_by} onChange={(event) => setBomDraft({ ...bomDraft, sponsored_by: event.target.value })} />
            </label>
            <button>Add</button>
          </form>}
          <div className="table-wrap bom-table-wrap">
            <table className="bom-table">
              <thead>
                <tr>{isMaster && <th className="bom-team-col">Team</th>}<th className="bom-category-col">Category</th><th className="bom-item-col">Item</th><th className="bom-qty-col">Qty</th><th className="bom-cost-col">Unit Cost (SGD)</th><th className="bom-total-col">Total (SGD)</th><th className="bom-sponsor-col">Sponsor</th>{bomExpanded && <th className="bom-version-col">Version</th>}{bomExpanded && <th className="bom-actions-col"></th>}</tr>
              </thead>
              <tbody>
                {groupedBomEntries.map(([category, items]) => (
                  <Fragment key={category}>
                    <tr className="bom-category-row">
                      <td colSpan={isMaster ? (bomExpanded ? "9" : "7") : (bomExpanded ? "8" : "6")}>{category}</td>
                    </tr>
                    {items.map((item) => (
                      <BomRow key={item.id} item={item} token={token} teams={teams} isMaster={isMaster} canEdit={canEdit && bomExpanded} expanded={bomExpanded} categories={bomCategories} history={history[item.id]} onLoadHistory={loadHistory} onCloseHistory={closeHistory} onRefresh={onRefresh} />
                    ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div>
          <div className="section-head">
            <h2>Budget Logs</h2>
            <div className="tab-switch">
              <button type="button" className={budgetTab === "logs" ? "active" : ""} onClick={() => setBudgetTab("logs")}>Logs</button>
              <button type="button" className={budgetTab === "invoices" ? "active" : ""} onClick={() => setBudgetTab("invoices")}>Invoices</button>
            </div>
          </div>
          {budgetTab === "logs" ? (
            <>
              {canEdit && <form className="inline-form budget-form" onSubmit={addBudget}>
                {isMaster ? (
                  <label className="compact-field">
                    <span>Team</span>
                    <select value={budgetDraft.team_id || ""} onChange={(event) => setBudgetDraft({ ...budgetDraft, team_id: Number(event.target.value) })}>
                      <option value="">Team</option>
                      {teams.map((team) => <option value={team.id} key={team.id}>{team.code}</option>)}
                    </select>
                  </label>
                ) : (
                  <label className="compact-field">
                    <span>Team</span>
                    <div className="locked-field">{teamName(teams, defaultTeam)}</div>
                  </label>
                )}
                <label className="compact-field budget-category-field">
                  <span>Category</span>
                  <input placeholder="Category" value={budgetDraft.category} onChange={(event) => setBudgetDraft({ ...budgetDraft, category: event.target.value })} />
                </label>
                <label className="compact-field">
                  <span>Amount</span>
                  <input type="number" step="0.01" value={budgetDraft.amount} onChange={(event) => setBudgetDraft({ ...budgetDraft, amount: event.target.value })} />
                </label>
                <label className="compact-field">
                  <span>Date</span>
                  <input type="date" value={budgetDraft.date} onChange={(event) => setBudgetDraft({ ...budgetDraft, date: event.target.value })} />
                </label>
                <label className="compact-field budget-notes-field">
                  <span>Notes</span>
                  <input placeholder="Notes" value={budgetDraft.notes} onChange={(event) => setBudgetDraft({ ...budgetDraft, notes: event.target.value })} />
                </label>
                <label className="compact-field budget-sponsor-field">
                  <span>Sponsor</span>
                  <input placeholder="Optional" value={budgetDraft.sponsored_by} onChange={(event) => setBudgetDraft({ ...budgetDraft, sponsored_by: event.target.value })} />
                </label>
                <button className="budget-add-button">Add</button>
              </form>}
              <div className="log-list">
                {budget.map((log) => (
                  <BudgetLogCard key={log.id} log={log} teams={teams} isMaster={isMaster} canEdit={canEdit} onPatch={patchBudget} onDelete={deleteBudget} />
                ))}
              </div>
            </>
          ) : (
            <div className="invoice-panel">
              {canEdit && (
                <form className="invoice-form" onSubmit={uploadInvoice}>
                  <label className="compact-field invoice-description-field">
                    <span>Description</span>
                    <input placeholder="Invoice description" value={invoiceDraft.description} onChange={(event) => setInvoiceDraft({ ...invoiceDraft, description: event.target.value })} />
                  </label>
                  <label className="compact-field">
                    <span>PDF Invoice</span>
                    <input type="file" accept="application/pdf,.pdf" onChange={(event) => setInvoiceDraft({ ...invoiceDraft, file: event.target.files?.[0] || null })} />
                  </label>
                  <button>Upload Invoice</button>
                </form>
              )}
              <div className="log-list">
                {invoices.map((invoice) => (
                  <article className="budget-log-card" key={invoice.id}>
                    <div>
                      <strong>{invoice.description}</strong>
                      <span>{invoice.original_filename} - {shortDate(invoice.uploaded_at?.slice(0, 10))}</span>
                    </div>
                    <div className="row-actions">
                      <button type="button" onClick={() => viewInvoice(invoice)}>View PDF</button>
                      {canEdit && <button className="danger-button" onClick={() => deleteInvoice(invoice)}>Delete</button>}
                    </div>
                  </article>
                ))}
                {invoices.length === 0 && <p className="empty-note">No invoices uploaded yet.</p>}
              </div>
            </div>
          )}
        </div>
      </div>
      {showPlan && (
        <BudgetPlanModal
          token={token}
          teams={teams}
          teamSummaries={dashboard?.team_summaries || []}
          selectedTeam={selectedTeam}
          plannedBudget={dashboard?.planned_budget || 0}
          canEdit={canEdit}
          onRefresh={onRefresh}
          onClose={() => setShowPlan(false)}
        />
      )}
    </section>
  );
}

function BudgetLogCard({ log, teams, isMaster, canEdit, onPatch, onDelete }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ team_id: log.team_id, category: log.category, amount: log.amount, date: log.date, notes: log.notes || "", sponsored_by: log.sponsored_by || "" });

  useEffect(() => {
    setDraft({ team_id: log.team_id, category: log.category, amount: log.amount, date: log.date, notes: log.notes || "", sponsored_by: log.sponsored_by || "" });
    setEditing(false);
  }, [log]);

  async function save() {
    await onPatch(log.id, {
      team_id: draft.team_id ? Number(draft.team_id) : null,
      category: draft.category.trim(),
      amount: Number(draft.amount),
      date: draft.date,
      notes: draft.notes.trim(),
      sponsored_by: draft.sponsored_by.trim(),
    });
    setEditing(false);
  }

  return (
    <article className="budget-log-card">
      {editing ? (
        <>
          <div className="budget-log-edit-grid">
            {isMaster && (
              <select value={draft.team_id || ""} onChange={(event) => setDraft({ ...draft, team_id: Number(event.target.value) })}>
                {teams.map((team) => <option value={team.id} key={team.id}>{team.code}</option>)}
              </select>
            )}
            <input value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })} />
            <input type="number" min="0" step="0.01" value={draft.amount} onChange={(event) => setDraft({ ...draft, amount: event.target.value })} />
            <input type="date" value={draft.date} onChange={(event) => setDraft({ ...draft, date: event.target.value })} />
            <input value={draft.notes} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} />
            <input placeholder="Sponsor" value={draft.sponsored_by} onChange={(event) => setDraft({ ...draft, sponsored_by: event.target.value })} />
          </div>
          <div className="row-actions">
            <button onClick={save}>Save</button>
            <button onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </>
      ) : (
        <>
          <div>
            <strong>{teamCode(teams, log.team_id)} - {log.category}</strong>
            <span>{money(log.amount)} - {shortDate(log.date)}</span>
            {log.sponsored_by && <span>Sponsored by {log.sponsored_by}</span>}
            <p>{log.notes}</p>
          </div>
          {canEdit && (
            <div className="row-actions">
              <button onClick={() => setEditing(true)}>Edit</button>
              <button className="danger-button" onClick={() => onDelete(log)}>Delete</button>
            </div>
          )}
        </>
      )}
    </article>
  );
}

function BudgetPlanModal({ token, teams, teamSummaries, selectedTeam, plannedBudget, canEdit, onRefresh, onClose }) {
  const [edits, setEdits] = useState({});
  const [savingTeamId, setSavingTeamId] = useState(null);
  const summaryByTeam = new Map(teamSummaries.map((summary) => [summary.id, summary]));
  const baseRows = teams
    .filter((team) => selectedTeam === "master" || String(team.id) === String(selectedTeam))
    .map((team) => {
      const summary = summaryByTeam.get(team.id) || {};
      const allocation = Number(team.budget ?? summary.budget ?? 0);
      const actualSpend = Number(summary.actual_spend || 0);
      return {
        ...team,
        allocation,
        actualSpend,
        remaining: allocation - actualSpend,
      };
    });
  const allocationRows = baseRows.map((team) => {
    const allocation = Number(edits[team.id] ?? team.allocation);
    return {
      ...team,
      allocation,
      remaining: allocation - team.actualSpend,
    };
  });
  const allocationTotal = allocationRows.reduce((total, team) => total + team.allocation, 0);

  useEffect(() => {
    setEdits(Object.fromEntries(baseRows.map((team) => [team.id, team.allocation])));
  }, [teams, teamSummaries, selectedTeam]);

  async function saveAllocation(teamId) {
    setSavingTeamId(teamId);
    try {
      await apiFetch(`/teams/${teamId}`, token, { method: "PATCH", body: JSON.stringify({ budget: Number(edits[teamId] || 0) }) });
      await onRefresh();
    } finally {
      setSavingTeamId(null);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <article className="task-detail-modal budget-plan-modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <span>Team Allocation</span>
            <h2>{money(plannedBudget)}</h2>
          </div>
          <button onClick={onClose}>Close</button>
        </header>
        <div className="plan-summary">
          <article>
            <span>Allocated to teams</span>
            <strong>{money(allocationTotal)}</strong>
          </article>
          <article>
            <span>Unallocated</span>
            <strong>{money(plannedBudget - allocationTotal)}</strong>
          </article>
        </div>
        <div className="table-wrap plan-table-wrap">
          <table>
            <thead>
              <tr><th>Team</th><th>Domain</th><th>Allocation</th><th>Actual Spend</th><th>Remaining</th><th>Share</th>{canEdit && <th></th>}</tr>
            </thead>
            <tbody>
              {allocationRows.map((team) => (
                <tr key={team.id}>
                  <td>
                    <strong>{team.code}</strong>
                    <span>{team.name}</span>
                  </td>
                  <td>{team.domain}</td>
                  <td>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={edits[team.id] ?? 0}
                      disabled={!canEdit}
                      onChange={(event) => setEdits({ ...edits, [team.id]: event.target.value })}
                    />
                  </td>
                  <td>{money(team.actualSpend)}</td>
                  <td className={team.remaining < 0 ? "over-budget" : ""}>{money(team.remaining)}</td>
                  <td>{allocationTotal ? `${Math.round((team.allocation / allocationTotal) * 100)}%` : "0%"}</td>
                  {canEdit && (
                    <td>
                      <button onClick={() => saveAllocation(team.id)} disabled={savingTeamId === team.id}>
                        {savingTeamId === team.id ? "Saving" : "Save"}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
              {allocationRows.length === 0 && (
                <tr>
                  <td colSpan={canEdit ? "7" : "6"}>No team allocation entries yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </article>
    </div>
  );
}

function BomRow({ item, token, teams, isMaster, canEdit, expanded, categories, history, onLoadHistory, onCloseHistory, onRefresh }) {
  const categoryOptionsId = `bom-category-options-${item.id}`;
  const [edit, setEdit] = useState({ team_id: item.team_id, category: item.category || "", name: item.name, quantity: item.quantity, unit_cost: item.unit_cost, sponsored_by: item.sponsored_by || "" });

  useEffect(() => {
    setEdit({ team_id: item.team_id, category: item.category || "", name: item.name, quantity: item.quantity, unit_cost: item.unit_cost, sponsored_by: item.sponsored_by || "" });
  }, [item]);

  async function save() {
    await apiFetch(`/bom/${item.id}`, token, {
      method: "PATCH",
      body: JSON.stringify({
        ...edit,
        team_id: edit.team_id ? Number(edit.team_id) : null,
        category: edit.category.trim(),
        quantity: Number(edit.quantity),
        unit_cost: Number(edit.unit_cost),
        sponsored_by: edit.sponsored_by.trim(),
      }),
    });
    await onRefresh();
  }

  async function removeItem() {
    if (!window.confirm(`Delete BOM item "${item.name}" and its version history?`)) return;
    await apiFetch(`/bom/${item.id}`, token, { method: "DELETE" });
    await onRefresh();
  }

  async function rollback(versionId) {
    if (!window.confirm("Roll back this BOM item to the selected history version? This will replace the current values without creating a new version.")) return;
    await apiFetch(`/bom/${item.id}/rollback/${versionId}`, token, { method: "POST" });
    await onRefresh();
    await onLoadHistory(item.id);
  }

  async function deleteVersion(versionId) {
    if (!window.confirm("Delete this BOM history version? The visible version number will be recalculated.")) return;
    await apiFetch(`/bom-version/${versionId}`, token, { method: "DELETE" });
    await onRefresh();
    await onLoadHistory(item.id);
  }

  return (
    <>
      <tr>
        {isMaster && (
          <td>
            {expanded ? (
              <select value={edit.team_id || ""} disabled={!canEdit} onChange={(event) => setEdit({ ...edit, team_id: event.target.value ? Number(event.target.value) : null })}>
                <option value="">General</option>
                {teams.map((team) => <option value={team.id} key={team.id}>{team.code}</option>)}
              </select>
            ) : teamCode(teams, item.team_id)}
          </td>
        )}
        <td className="bom-category-col">
          {expanded ? (
            <>
              <datalist id={categoryOptionsId}>
                {categories.map((category) => <option value={category} key={category} />)}
              </datalist>
              <input list={categoryOptionsId} placeholder="Category" value={edit.category} disabled={!canEdit} onChange={(event) => setEdit({ ...edit, category: event.target.value })} />
            </>
          ) : item.category || "Uncategorized"}
        </td>
        <td className="bom-item-col">{expanded ? <input value={edit.name} disabled={!canEdit} onChange={(event) => setEdit({ ...edit, name: event.target.value })} /> : item.name}</td>
        <td className="bom-qty-col">{expanded ? <input type="number" step="1" value={edit.quantity} disabled={!canEdit} onChange={(event) => setEdit({ ...edit, quantity: event.target.value })} /> : item.quantity}</td>
        <td className="bom-cost-col">{expanded ? <input type="number" step="0.01" value={edit.unit_cost} disabled={!canEdit} onChange={(event) => setEdit({ ...edit, unit_cost: event.target.value })} /> : money(item.unit_cost)}</td>
        <td className="bom-total-col">{money(item.total_cost)}</td>
        <td className="bom-sponsor-col">{expanded ? <input placeholder="Sponsor" value={edit.sponsored_by} disabled={!canEdit} onChange={(event) => setEdit({ ...edit, sponsored_by: event.target.value })} /> : item.sponsored_by || "-"}</td>
        {expanded && <td className="bom-version-col">v{item.version}</td>}
        {expanded && (
          <td className="row-actions bom-actions-col">
            {canEdit && <button onClick={save}>Save</button>}
            <button onClick={() => onLoadHistory(item.id)}>{history ? "Refresh" : "History"}</button>
            {canEdit && <button className="danger-button" onClick={removeItem}>Delete</button>}
          </td>
        )}
      </tr>
      {expanded && history && (
        <tr className="history-row">
          <td colSpan={isMaster ? "9" : "8"}>
            <div className="history-panel">
              <header>
                <strong>Version history</strong>
                <button onClick={() => onCloseHistory(item.id)}>Close</button>
              </header>
              {history.length === 0 && <span>No previous versions.</span>}
              {history.map((version) => (
                <div className="history-version" key={version.id}>
                  <span>v{version.version}: {version.category ? `${version.category} - ` : ""}{version.name} - {money(version.total_cost)}{version.sponsored_by ? ` - ${version.sponsored_by}` : ""}</span>
                  <div>
                    {canEdit && <button onClick={() => rollback(version.id)}>Roll Back</button>}
                    {canEdit && <button className="danger-button" onClick={() => deleteVersion(version.id)}>Delete Version</button>}
                  </div>
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function Assets({ projectId, teamId, selectedTeam, token, teams, assets, canEdit, onRefresh }) {
  const isMaster = selectedTeam === "master";
  const defaultTeam = isMaster ? null : teamId || teams[0]?.id || null;
  const emptyDraft = {
    project_id: projectId,
    team_id: defaultTeam,
    category: "",
    name: "",
    source: "owned",
    provider: "",
    quantity: 1,
    estimated_value: 0,
    notes: "",
  };
  const [draft, setDraft] = useState(emptyDraft);
  const categories = [...new Set(assets.map((asset) => asset.category?.trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  const totalAssets = assets.reduce((total, asset) => total + Number(asset.quantity || 0), 0);
  const loanedCount = assets.filter((asset) => asset.source === "loaned").length;
  const sponsoredValue = assets.filter((asset) => asset.source === "sponsored").reduce((total, asset) => total + Number(asset.estimated_value || 0), 0);

  useEffect(() => {
    setDraft({
      project_id: projectId,
      team_id: defaultTeam,
      category: "",
      name: "",
      source: "owned",
      provider: "",
      quantity: 1,
      estimated_value: 0,
      notes: "",
    });
  }, [projectId, defaultTeam, selectedTeam]);

  async function addAsset(event) {
    event.preventDefault();
    if (!draft.name.trim()) return;
    await apiFetch("/assets", token, {
      method: "POST",
      body: JSON.stringify({
        ...draft,
        project_id: projectId,
        team_id: draft.team_id ? Number(draft.team_id) : null,
        category: draft.category.trim(),
        name: draft.name.trim(),
        asset_tag: "",
        provider: draft.provider.trim(),
        quantity: Number(draft.quantity),
        estimated_value: Number(draft.estimated_value),
        condition: "",
        location: "",
        assigned_to: "",
        start_date: null,
        end_date: null,
        notes: draft.notes.trim(),
      }),
    });
    setDraft({ ...emptyDraft, category: draft.category.trim(), source: draft.source });
    await onRefresh();
  }

  async function patchAsset(id, patch) {
    await apiFetch(`/assets/${id}`, token, { method: "PATCH", body: JSON.stringify(patch) });
    await onRefresh();
  }

  async function deleteAsset(asset) {
    if (!window.confirm(`Delete asset "${asset.name}"?`)) return;
    await apiFetch(`/assets/${asset.id}`, token, { method: "DELETE" });
    await onRefresh();
  }

  return (
    <section className="stack">
      <div className="metrics-grid">
        <Metric label="Asset Qty" value={totalAssets} detail="Total equipment quantity in this scope" />
        <Metric label="Loaned" value={loanedCount} detail="Assets currently marked as loaned" />
        <Metric label="Sponsored" value={money(sponsoredValue)} detail="Estimated sponsored asset value" />
        <Metric label="Records" value={assets.length} detail="Tracked equipment entries" />
      </div>

      {canEdit && (
        <form className="asset-form" onSubmit={addAsset}>
          <datalist id="asset-category-options">
            {categories.map((category) => <option value={category} key={category} />)}
          </datalist>
          <label className="compact-field">
            <span>Team</span>
            {isMaster ? (
              <select value={draft.team_id || ""} onChange={(event) => setDraft({ ...draft, team_id: event.target.value ? Number(event.target.value) : null })}>
                <option value="">General</option>
                {teams.map((team) => <option value={team.id} key={team.id}>{team.code}</option>)}
              </select>
            ) : (
              <div className="locked-field">{teamName(teams, defaultTeam)}</div>
            )}
          </label>
          <label className="compact-field">
            <span>Category</span>
            <input list="asset-category-options" placeholder="Category" value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })} />
          </label>
          <label className="compact-field">
            <span>Asset</span>
            <input placeholder="Equipment / asset name" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
          </label>
          <label className="compact-field">
            <span>Source</span>
            <select value={draft.source} onChange={(event) => setDraft({ ...draft, source: event.target.value })}>
              <option value="owned">Owned</option>
              <option value="loaned">Loaned</option>
              <option value="sponsored">Sponsored</option>
            </select>
          </label>
          <label className="compact-field">
            <span>Provider</span>
            <input placeholder="Sponsor / lender" value={draft.provider} onChange={(event) => setDraft({ ...draft, provider: event.target.value })} />
          </label>
          <label className="compact-field">
            <span>Qty</span>
            <input type="number" min="0" step="1" value={draft.quantity} onChange={(event) => setDraft({ ...draft, quantity: event.target.value })} />
          </label>
          <label className="compact-field">
            <span>Value</span>
            <input type="number" min="0" step="0.01" value={draft.estimated_value} onChange={(event) => setDraft({ ...draft, estimated_value: event.target.value })} />
          </label>
          <label className="compact-field asset-notes-field">
            <span>Notes</span>
            <input placeholder="Notes" value={draft.notes} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} />
          </label>
          <button>Add Asset</button>
        </form>
      )}

      <div className="table-wrap">
        <table className="asset-table">
          <thead>
            <tr>
              <th>Team</th><th>Category</th><th>Asset</th><th>Source</th><th>Provider</th><th>Qty</th><th>Value</th><th>Notes</th>{canEdit && <th></th>}
            </tr>
          </thead>
          <tbody>
            {assets.map((asset) => (
              <AssetRow key={asset.id} asset={asset} teams={teams} canEdit={canEdit} onPatch={patchAsset} onDelete={deleteAsset} />
            ))}
            {assets.length === 0 && (
              <tr><td colSpan={canEdit ? "9" : "8"}>No equipment or assets tracked yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function AssetRow({ asset, teams, canEdit, onPatch, onDelete }) {
  const [draft, setDraft] = useState({ ...asset });

  useEffect(() => {
    setDraft({ ...asset });
  }, [asset]);

  async function saveAsset() {
    await onPatch(asset.id, {
      team_id: draft.team_id ? Number(draft.team_id) : null,
      category: draft.category || "",
      name: draft.name,
      source: draft.source,
      provider: draft.provider || "",
      quantity: Number(draft.quantity),
      estimated_value: Number(draft.estimated_value),
      notes: draft.notes || "",
    });
  }

  return (
    <tr>
      <td>
        <select value={draft.team_id || ""} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, team_id: event.target.value ? Number(event.target.value) : null })}>
          <option value="">General</option>
          {teams.map((team) => <option value={team.id} key={team.id}>{team.code}</option>)}
        </select>
      </td>
      <td><input value={draft.category || ""} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, category: event.target.value })} /></td>
      <td><input value={draft.name} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></td>
      <td>
        <select value={draft.source} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, source: event.target.value })}>
          <option value="owned">Owned</option>
          <option value="loaned">Loaned</option>
          <option value="sponsored">Sponsored</option>
        </select>
      </td>
      <td><input value={draft.provider || ""} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, provider: event.target.value })} /></td>
      <td><input type="number" min="0" step="1" value={draft.quantity} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, quantity: event.target.value })} /></td>
      <td>
        {canEdit ? (
          <input placeholder="NA" type={Number(draft.estimated_value || 0) ? "number" : "text"} min="0" step="0.01" value={Number(draft.estimated_value || 0) ? draft.estimated_value : "NA"} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, estimated_value: event.target.value === "NA" ? 0 : event.target.value })} onFocus={() => Number(draft.estimated_value || 0) === 0 && setDraft({ ...draft, estimated_value: "" })} onBlur={() => draft.estimated_value === "" && setDraft({ ...draft, estimated_value: 0 })} />
        ) : (
          Number(draft.estimated_value || 0) ? money(draft.estimated_value) : "NA"
        )}
      </td>
      <td><input value={draft.notes || ""} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} /></td>
      {canEdit && (
        <td className="row-actions">
          <button onClick={saveAsset}>Save</button>
          <button className="danger-button" onClick={() => onDelete(asset)}>Delete</button>
        </td>
      )}
    </tr>
  );
}

function Members({ teamId, selectedTeam, token, teams, users, canEdit, onRefresh }) {
  const defaultTeam = teamId || teams[0]?.id || null;
  const isMaster = selectedTeam === "master";
  const [draft, setDraft] = useState({ team_id: defaultTeam, name: "", role: "engineer" });
  const visibleUsers = isMaster ? users : users.filter((user) => user.team_id === defaultTeam);

  useEffect(() => {
    setDraft({ team_id: defaultTeam, name: "", role: "engineer" });
  }, [defaultTeam, selectedTeam]);

  async function addMember(event) {
    event.preventDefault();
    if (!draft.name.trim() || !draft.team_id) return;
    await apiFetch("/users", token, { method: "POST", body: JSON.stringify({ ...draft, team_id: Number(draft.team_id) }) });
    setDraft({ team_id: defaultTeam, name: "", role: "engineer" });
    await onRefresh();
  }

  return (
    <section className="stack">
      {canEdit && <form className="member-form" onSubmit={addMember}>
        {isMaster ? (
          <select value={draft.team_id || ""} onChange={(event) => setDraft({ ...draft, team_id: Number(event.target.value) })}>
            <option value="">Team</option>
            {teams.map((team) => <option value={team.id} key={team.id}>{team.code}</option>)}
          </select>
        ) : (
          <div className="locked-field">{teamName(teams, defaultTeam)}</div>
        )}
        <input placeholder="Member name" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
        <input placeholder="Role" value={draft.role} onChange={(event) => setDraft({ ...draft, role: event.target.value })} />
        <button>Add Member</button>
      </form>}

      <div className="table-wrap">
        <table className="member-table">
          <thead>
            <tr>{isMaster && <th>Team</th>}<th>Name</th><th>Role</th>{canEdit && <th></th>}</tr>
          </thead>
          <tbody>
            {visibleUsers.map((user) => (
              <MemberRow key={user.id} user={user} teams={teams} isMaster={isMaster} token={token} canEdit={canEdit} onRefresh={onRefresh} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function MemberRow({ user, teams, isMaster, token, canEdit, onRefresh }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ team_id: user.team_id, name: user.name, role: user.role });

  useEffect(() => {
    setDraft({ team_id: user.team_id, name: user.name, role: user.role });
    setEditing(false);
  }, [user]);

  async function saveMember() {
    await apiFetch(`/users/${user.id}`, token, { method: "PATCH", body: JSON.stringify({ ...draft, team_id: Number(draft.team_id) }) });
    setEditing(false);
    await onRefresh();
  }

  async function deleteMember() {
    if (!window.confirm(`Delete member "${user.name}"? Existing tasks keep their owner text until reassigned.`)) return;
    await apiFetch(`/users/${user.id}`, token, { method: "DELETE" });
    await onRefresh();
  }

  return (
    <tr>
      {isMaster && (
        <td>
          {editing ? (
            <select value={draft.team_id || ""} onChange={(event) => setDraft({ ...draft, team_id: Number(event.target.value) })}>
              {teams.map((team) => <option value={team.id} key={team.id}>{team.code}</option>)}
            </select>
          ) : teamCode(teams, user.team_id)}
        </td>
      )}
      <td>{editing ? <input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /> : user.name}</td>
      <td>{editing ? <input value={draft.role} onChange={(event) => setDraft({ ...draft, role: event.target.value })} /> : user.role}</td>
      {canEdit && (
        <td className="row-actions">
          {editing ? (
            <>
              <button onClick={saveMember}>Save</button>
              <button onClick={() => setEditing(false)}>Cancel</button>
            </>
          ) : (
            <button onClick={() => setEditing(true)}>Edit</button>
          )}
          <button className="danger-button" onClick={deleteMember}>Delete</button>
        </td>
      )}
    </tr>
  );
}

function Sponsors({ projectId, selectedTeam, token, teams, sponsors, bom, budget, assets, dashboard, canEdit, onRefresh }) {
  const [draft, setDraft] = useState({ project_id: projectId, team_id: null, name: "", amount: 0, date: today, notes: "" });
  const sponsoredSupport = [
    ...(bom || [])
      .filter((item) => item.sponsored_by?.trim())
      .map((item) => ({
        id: `bom-${item.id}`,
        sponsor: item.sponsored_by,
        type: "Material",
        team_id: item.team_id,
        name: item.name,
        value: item.total_cost,
        notes: `${item.category ? `${item.category} - ` : ""}${item.quantity} x ${money(item.unit_cost)}`,
      })),
    ...(budget || [])
      .filter((log) => log.sponsored_by?.trim())
      .map((log) => ({
        id: `log-${log.id}`,
        sponsor: log.sponsored_by,
        type: "Service",
        team_id: log.team_id,
        name: log.category,
        value: log.amount,
        notes: log.notes || shortDate(log.date),
      })),
    ...(assets || [])
      .filter((asset) => asset.source === "sponsored" && asset.provider?.trim())
      .map((asset) => ({
        id: `asset-${asset.id}`,
        sponsor: asset.provider,
        type: "Asset",
        team_id: asset.team_id,
        name: asset.name,
        value: asset.estimated_value || 0,
        notes: `${asset.category || "Asset"}${asset.asset_tag ? ` - ${asset.asset_tag}` : ""}`,
      })),
  ];
  const inKindTotal = sponsoredSupport.reduce((total, item) => total + Number(item.value || 0), 0);

  useEffect(() => {
    setDraft({ project_id: projectId, team_id: null, name: "", amount: 0, date: today, notes: "" });
  }, [projectId]);

  async function addSponsor(event) {
    event.preventDefault();
    if (!draft.name.trim()) return;
    await apiFetch("/sponsors", token, { method: "POST", body: JSON.stringify({ ...draft, project_id: projectId, team_id: null, amount: Number(draft.amount) }) });
    setDraft({ project_id: projectId, team_id: null, name: "", amount: 0, date: today, notes: "" });
    await onRefresh();
  }

  async function patchSponsor(id, patch) {
    await apiFetch(`/sponsors/${id}`, token, { method: "PATCH", body: JSON.stringify(patch) });
    await onRefresh();
  }

  async function deleteSponsor(sponsor) {
    if (!window.confirm(`Delete sponsor entry "${sponsor.name}"? Planned budget will update immediately.`)) return;
    await apiFetch(`/sponsors/${sponsor.id}`, token, { method: "DELETE" });
    await onRefresh();
  }

  return (
    <section className="stack">
      <div className="metrics-grid">
        {selectedTeam === "master" && <Metric label="Money In" value={money(dashboard?.sponsor_total)} detail="Sponsor funding in this scope" />}
        <Metric label="In-kind" value={money(inKindTotal)} detail="Sponsored materials and services" />
        <Metric label="Planned Budget" value={money(dashboard?.planned_budget)} detail="Used by dashboard and budget remaining" />
        <Metric label="Remaining" value={money(dashboard?.remaining_budget)} detail="Cash planned minus actual" tone={dashboard?.remaining_budget < 0 ? "danger" : ""} />
      </div>

      {canEdit && <form className="sponsor-form" onSubmit={addSponsor}>
        <input placeholder="Sponsor" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
        <input type="number" step="0.01" min="0" value={draft.amount} onChange={(event) => setDraft({ ...draft, amount: event.target.value })} />
        <input type="date" value={draft.date} onChange={(event) => setDraft({ ...draft, date: event.target.value })} />
        <input placeholder="Notes" value={draft.notes} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} />
        <button>Add Money In</button>
      </form>}

      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Sponsor</th><th>Amount</th><th>Date</th><th>Notes</th>{canEdit && <th></th>}</tr>
          </thead>
          <tbody>
            {sponsors.map((sponsor) => (
              <tr key={sponsor.id}>
                <td><input value={sponsor.name} disabled={!canEdit} onChange={(event) => patchSponsor(sponsor.id, { name: event.target.value })} /></td>
                <td><input type="number" step="0.01" min="0" value={sponsor.amount} disabled={!canEdit} onChange={(event) => patchSponsor(sponsor.id, { amount: Number(event.target.value) })} /></td>
                <td><input type="date" value={sponsor.date} disabled={!canEdit} onChange={(event) => patchSponsor(sponsor.id, { date: event.target.value })} /></td>
                <td><input value={sponsor.notes || ""} disabled={!canEdit} onChange={(event) => patchSponsor(sponsor.id, { notes: event.target.value })} /></td>
                {canEdit && <td><button className="danger-button" onClick={() => deleteSponsor(sponsor)}>Delete</button></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <section className="panel-block">
        <div className="section-head">
          <h2>Sponsored Materials & Services</h2>
          <span>{money(inKindTotal)}</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Sponsor</th><th>Type</th><th>Team</th><th>Item / Service</th><th>Value</th><th>Notes</th></tr>
            </thead>
            <tbody>
              {sponsoredSupport.map((item) => (
                <tr key={item.id}>
                  <td>{item.sponsor}</td>
                  <td>{item.type}</td>
                  <td>{teamCode(teams, item.team_id)}</td>
                  <td>{item.name}</td>
                  <td>{money(item.value)}</td>
                  <td>{item.notes}</td>
                </tr>
              ))}
              {sponsoredSupport.length === 0 && (
                <tr><td colSpan="6">No sponsored materials or services yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}

function Blockers({ token, blockers, tasks, canEdit, onRefresh }) {
  const [draft, setDraft] = useState({ task_id: "", description: "", severity: "medium", status: "open" });

  async function addBlocker(event) {
    event.preventDefault();
    if (!draft.task_id || !draft.description.trim()) return;
    await apiFetch("/blockers", token, { method: "POST", body: JSON.stringify({ ...draft, task_id: Number(draft.task_id) }) });
    setDraft({ task_id: "", description: "", severity: "medium", status: "open" });
    await onRefresh();
  }

  async function updateBlocker(id, patch) {
    await apiFetch(`/blockers/${id}`, token, { method: "PATCH", body: JSON.stringify(patch) });
    await onRefresh();
  }

  return (
    <section className="stack">
      {canEdit && <form className="task-composer" onSubmit={addBlocker}>
        <select value={draft.task_id} onChange={(event) => setDraft({ ...draft, task_id: event.target.value })}>
          <option value="">Task</option>
          {tasks.map((task) => (
            <option value={task.id} key={task.id}>#{task.id} {task.title}</option>
          ))}
        </select>
        <input placeholder="Blocker description" value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
        <select value={draft.severity} onChange={(event) => setDraft({ ...draft, severity: event.target.value })}>
          <option>low</option>
          <option>medium</option>
          <option>high</option>
          <option>critical</option>
        </select>
        <button>Add Blocker</button>
      </form>}
      <div className="blocker-list">
        {blockers.map((blocker) => (
          <article key={blocker.id} className={blocker.status === "open" ? "open" : ""}>
            <div>
              <strong>{blocker.team_code ? `${blocker.team_code} - ` : ""}{blocker.task_title}</strong>
              <p>{blocker.description}</p>
            </div>
            <i className={severityClass(blocker.severity)}>{blocker.severity}</i>
            <select value={blocker.status} disabled={!canEdit} onChange={(event) => updateBlocker(blocker.id, { status: event.target.value })}>
              <option>open</option>
              <option>resolved</option>
            </select>
          </article>
        ))}
      </div>
    </section>
  );
}
