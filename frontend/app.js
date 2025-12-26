async function safeFetch(url, options = {}) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    alert("Session expired. Please login again.");
    logout();
    return null;
  }
  return res;
}
function formatLocalTime(utcString) {
  if (!utcString) return "-";

  // 🔥 FORCE UTC interpretation
  const utcDate = new Date(
    utcString.endsWith("Z") ? utcString : utcString + "Z"
  );

  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone
  }).format(utcDate);
}/* =========================================================
   USER CACHE (MENTIONS SOURCE)
========================================================= */
let ALL_USERS = []; // [{ username, userId }]
/* =========================================================
   GLOBAL SETUP
========================================================= */
let ALL_TASKS = []; // 🔑 source of truth for filtering

const API = "https://s8a2413duf.execute-api.ap-south-1.amazonaws.com/prod";
const token = localStorage.getItem("access_token");
if (!token) location.href = "login.html";

/* Decode JWT (access token) */
const claims = JSON.parse(atob(token.split(".")[1]));
const username = claims.username || claims.email;
const isAdmin = claims["cognito:groups"]?.includes("admin");

/* Header UI */
document.getElementById("username").innerText = username;
document.getElementById("roleBadge").innerText = isAdmin ? "ADMIN" : "USER";
document.getElementById("roleBadge").classList.add(isAdmin ? "admin" : "user");
if (isAdmin) document.getElementById("auditNav").style.display = "block";

function getGreeting(username) {
  const now = new Date();
  const hour = now.getHours();

  let greeting;
  let emoji = "";

  if (hour >= 5 && hour < 12) {
    greeting = "Good Morning";
    emoji = "☀️";
  } else if (hour >= 12 && hour < 17) {
    greeting = "Good Afternoon";
    emoji = "🌤️";
  } else if (hour >= 17 && hour < 21) {
    greeting = "Good Evening";
    emoji = "🌆";
  } else {
    greeting = "Working late";
    emoji = "🌙";
  }

  return `${greeting}, ${username} ${emoji}`;
}
document.getElementById("greetingText").innerText =
  getGreeting(username);
/* =========================================================
   MENTION HIGHLIGHTER (SAFE)
========================================================= */
function highlightMentions(text = "") {
  return text.replace(
    /@([a-zA-Z0-9_.-]+)/g,
    `<span style="
      color:#60a5fa;
      font-weight:600;
      cursor:pointer;
    ">@$1</span>`
  );
}
/* =========================================================
   VIEW SWITCHER
========================================================= */
function showView(v) {
  ["tasks", "mentions", "audit"].forEach(x =>
    document.getElementById(x + "View").classList.add("hidden")
  );
  document.getElementById(v + "View").classList.remove("hidden");

  if (v === "tasks") loadTasks();
  if (v === "mentions") loadMentions();
  if (v === "audit") loadAudit();
}

/* =========================================================
   BADGES
========================================================= */
function statusBadge(status) {
  return `<span class="status-badge status-${status}">${status}</span>`;
}

function priorityBadge(priority) {
  return `<span class="priority-badge priority-${priority}">${priority}</span>`;
}

function categoryBadge(category) {
  const c = category || "general";
  return `<span class="category-badge category-${c}">${c}</span>`;
}
function isMyTask(task) {
  return Array.isArray(task.participantIds) &&
         task.participantIds.includes(claims.sub);
}
/* =========================================================
   LOAD USERS (FOR @MENTIONS)
========================================================= */
async function loadUsers() {
  try {
    const res = await fetch(`${API}/users`, {
      headers: {
        Authorization: `Bearer ${token}`
      }
    });

    if (!res.ok) throw new Error("Failed to load users");

    const users = await res.json();

    // Normalize (safe)
    ALL_USERS = users
      .filter(u => u.username) // safety
      .map(u => ({
        username: u.username,
        userId: u.sub
      }));

    console.log("Users loaded for mentions:", ALL_USERS.length);
  } catch (err) {
    console.error("User load failed:", err);
  }
}
/* =========================================================
   TASKS — FETCH & STORE
========================================================= */
async function loadTasks() {
  const res = await fetch(`${API}/tasks`, {
    headers: { Authorization: `Bearer ${token}` }
  });

  ALL_TASKS = await res.json();

  /* Normalize identifiers once */
  ALL_TASKS.forEach(t => {
    t.id = t.taskId;
    t.pk = `TASK#${t.taskId}`;
  });

  ALL_TASKS.sort((a, b) =>
    new Date(b.updatedAt || b.createdAt) -
    new Date(a.updatedAt || a.createdAt)
  );

  renderTasks(ALL_TASKS);
  refreshMentionsBadge();
}
/* =========================================================
   SHARED USERS HELPER
========================================================= */
function sharedUsersBadge(task) {
  if (!Array.isArray(task.participants)) return "";

  const shared = task.participants.filter(
    p => p.userId !== task.ownerId
  );

  if (!shared.length) return "";

  const names = shared.map(p => p.userName);
  const visible = names.slice(0, 2).join(", ");
  const extra = names.length > 2 ? ` (+${names.length - 2})` : "";

  return `
    <div style="margin-top:6px;font-size:12px;color:#94a3b8;">
      Shared with: ${visible}${extra}
    </div>
  `;
}
/* =========================================================
   TASK RENDER (SINGLE SOURCE OF UI)
========================================================= */
function renderTasks(tasks) {
  const body = document.getElementById("taskBody");
  body.innerHTML = "";

  tasks.forEach((t, i) => {
    const canDelete = isAdmin || t.ownerId === claims.sub;

    body.innerHTML += `
<tr style="${isMyTask(t) ? 'background:#020617;border-left:4px solid #2563eb;' : ''}">
  <td>${i + 1}</td>

  <td>
  <div style="font-weight:600;">
  ${sanitizeInput(t.title)}
</div>

  <div style="margin-top:6px;">
    ${categoryBadge(t.category)}
  </div>

  ${sharedUsersBadge(t)}
</td>

  <td>
  ${t.createdBy || t.ownerName}
  ${t.ownerId === claims.sub ? '<span style="opacity:.6">(You)</span>' : ''}
</td>

  <!-- STATUS -->
  <td>
    ${statusBadge(t.status)}
    <div style="margin-top:6px;">
      <select onchange="updateTask('${t.id}','${t.pk}',{ status:this.value })">
        <option value="todo" ${t.status === "todo" ? "selected" : ""}>todo</option>
        <option value="in-progress" ${t.status === "in-progress" ? "selected" : ""}>in-progress</option>
        <option value="done" ${t.status === "done" ? "selected" : ""}>done</option>
      </select>
    </div>
  </td>

  <!-- PRIORITY -->
  <td>
    ${priorityBadge(t.priority)}
    <div style="margin-top:6px;">
      <select onchange="updateTask('${t.id}','${t.pk}',{ priority:this.value })">
        <option value="low" ${t.priority === "low" ? "selected" : ""}>low</option>
        <option value="medium" ${t.priority === "medium" ? "selected" : ""}>medium</option>
        <option value="high" ${t.priority === "high" ? "selected" : ""}>high</option>
      </select>
    </div>
  </td>

  <td>${formatLocalTime(t.updatedAt || t.createdAt)}</td>

  <!-- ACTIONS -->
  <td>
    <button class="btn btn-blue" onclick="loadComments('${t.id}')">
     Comments <span style="opacity:.7">
  (${t.commentCount || 0})
</span>
    </button>
    ${
      canDelete
        ? `<button class="btn btn-red" onclick="deleteTask('${t.id}','${t.pk}')">Delete</button>`
        : `<button class="btn btn-red" disabled style="opacity:0.4;cursor:not-allowed;">Delete</button>`
    }
  </td>
</tr>

<tr id="c-${t.id}" class="comment-row hidden">
  <td colspan="7">
    <div style="display:flex;gap:8px;margin-top:8px;">
      <input
  id="ci-${t.id}"
  placeholder="Write a comment (@username)">
      <button class="btn btn-green" onclick="addComment('${t.id}')">Send</button>
    </div>
    <div id="cl-${t.id}" style="margin-top:12px;"></div>
  </td>
</tr>`;
    
  });
}

/* =========================================================
   TASK ACTIONS
========================================================= */
async function updateTask(id, pk, body) {
  body.pk = pk;

  await fetch(`${API}/tasks/${id}`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body)
  });

  loadTasks();
}

async function addTask() {
  const rawTitle = title.value.trim();

  // ❌ Empty
  if (!rawTitle) {
    alert("Title cannot be empty");
    return;
  }

  // ❌ HTML / script attempt
  if (/[<>]/.test(rawTitle)) {
    alert("Title cannot contain HTML or special characters");
    return;
  }

  await fetch(`${API}/tasks`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      title: rawTitle,
      description: description.value,
      category: category.value,
      status: status.value,
      priority: priority.value
    })
  });

  title.value = "";
  description.value = "";
  category.value = "";
  loadTasks();
}

async function deleteTask(id, pk) {
  await fetch(`${API}/tasks/${id}`, {
    method: "DELETE",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ pk })
  });

  loadTasks();
}

/* =========================================================
   COMMENTS (OPEN / CLOSE + COUNT)
========================================================= */
async function loadComments(taskId) {
  const row = document.getElementById(`c-${taskId}`);
  if (!row) return;

  if (!row.classList.contains("hidden")) {
    row.classList.remove("open");
    setTimeout(() => row.classList.add("hidden"), 200);
    return;
  }

  row.classList.remove("hidden");
  setTimeout(() => row.classList.add("open"), 10);

  const res = await fetch(`${API}/tasks/${taskId}/comments`, {
    headers: { Authorization: `Bearer ${token}` }
  });

  let data = await res.json();

/// ✅ Newest → Oldest (latest on top)
data.sort((a, b) =>
  new Date(b.createdAt) - new Date(a.createdAt)
);

 
  document.getElementById(`cl-${taskId}`).innerHTML =
  data.map(c => {
    const canModify =
      isAdmin || c.userId === claims.sub;

    return `
<div style="margin-top:8px;padding:10px;border-radius:8px;
            background:#020617;border:1px solid #1e293b;">

  <div style="display:flex;justify-content:space-between;">
    <div style="font-size:13px;color:#60a5fa;font-weight:600;">
      ${c.userName}
    </div>

    ${
      canModify
        ? `
        <div style="display:flex;gap:6px;">
          <button class="btn btn-blue"
                  onclick="startEditComment('${taskId}','${c.commentId}')">
            Edit
          </button>
          <button class="btn btn-red"
                  onclick="deleteComment('${taskId}','${c.commentId}')">
            Delete
          </button>
        </div>`
        : ""
    }
  </div>

 <div id="ct-${c.commentId}" style="margin-top:6px;">
  ${highlightMentions(c.comment)}
</div>

  <div style="margin-top:6px;font-size:11px;color:#94a3b8;">
    ${formatLocalTime(c.createdAt)}
  </div>
</div>`;
  }).join("");
}

async function addComment(taskId) {
  const input = document.getElementById(`ci-${taskId}`);
  const v = input.value.trim();
  if (!v) return;

  const btn = input.nextElementSibling;
  btn.disabled = true;

  await fetch(`${API}/tasks/${taskId}/comments`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ comment: v })
  });

  input.value = "";
  btn.disabled = false;

  // ✅ reload comments only
  await loadComments(taskId);

  // ✅ update count safely
  const task = ALL_TASKS.find(t => t.id === taskId);
  if (task) task.commentCount = (task.commentCount || 0) + 1;

  // update button text only
  const btnLabel = document.querySelector(
    `button[onclick="loadComments('${taskId}')"]`
  );
  if (btnLabel) btnLabel.innerText = `Comments (${task.commentCount})`;
}


function startEditComment(taskId, commentId) {
  const container = document.getElementById(`ct-${commentId}`);
  const current = container.innerText;

  container.innerHTML = `
    <textarea id="edit-${commentId}"
      style="width:100%;background:#020617;
             border:1px solid #1e293b;color:#e5e7eb;
             border-radius:8px;padding:6px;">${current}</textarea>

    <div style="margin-top:6px;display:flex;gap:8px;">
      <button class="btn btn-green"
        onclick="saveCommentEdit('${taskId}','${commentId}')">
        Save
      </button>
      <button class="btn btn-red"
        onclick="loadComments('${taskId}')">
        Cancel
      </button>
    </div>
  `;
}
async function saveCommentEdit(taskId, commentId) {
  const v = document.getElementById(`edit-${commentId}`).value.trim();
  if (!v) return;

  await fetch(`${API}/tasks/${taskId}/comments/${commentId}`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ comment: v })
  });

  loadComments(taskId); // refresh
}
async function deleteComment(taskId, commentId) {
  if (!confirm("Delete this comment?")) return;

  await fetch(`${API}/tasks/${taskId}/comments/${commentId}`, {
    method: "DELETE",
    headers: {
      Authorization: `Bearer ${token}`
    }
  });
 const task = ALL_TASKS.find(t => t.id === taskId);
if (task && task.commentCount > 0) task.commentCount--;
renderTasks(ALL_TASKS);
  loadComments(taskId);
}

async function editComment(taskId, commentId, oldText) {
  const newText = prompt("Edit comment", oldText);
  if (!newText || newText.trim() === oldText) return;

  const res = await fetch(
    `${API}/tasks/${taskId}/comments/${commentId}`,
    {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        comment: newText.trim()
      })
    }
  );

  if (!res.ok) {
    const err = await res.text();
    alert("Edit failed: " + err);
    return;
  }

  loadComments(taskId);
}


/* =========================================================
   TASK FILTERING (CATEGORY + STATUS + PRIORITY)
========================================================= */
function applyFilters() {
  const status = document.getElementById("filterStatus").value;
  const priority = document.getElementById("filterPriority").value;
  const category = document.getElementById("filterCategory")?.value;

  let filtered = [...ALL_TASKS];

  if (category) filtered = filtered.filter(t => t.category === category);
  if (status) filtered = filtered.filter(t => t.status === status);
  if (priority) filtered = filtered.filter(t => t.priority === priority);

  renderTasks(filtered);
}

function clearFilters() {
  document.getElementById("filterStatus").value = "";
  document.getElementById("filterPriority").value = "";
  if (document.getElementById("filterCategory"))
    document.getElementById("filterCategory").value = "";

  renderTasks(ALL_TASKS);
}

/* =========================================================
   MENTIONS (UNCHANGED)
========================================================= */
async function loadMentions() {
  const res = await fetch(`${API}/mentions`, {
    headers: { Authorization: `Bearer ${token}` }
  });

  const mentions = await res.json();
  const badge = document.getElementById("mentionsBadge");

  const unread = mentions.filter(m => m.status !== "READ").length;
  badge.style.display = unread ? "inline-block" : "none";
  badge.innerText = unread || "";

  const body = document.getElementById("mentionsBody");
  body.innerHTML = "";

  if (!mentions.length) {
    body.innerHTML = `<tr><td colspan="5" class="subtitle">No mentions 🎉</td></tr>`;
    return;
  }

  mentions.forEach(m => {
    const date =
      m.displayDate ||
      (m.sk?.startsWith("MENTION#")
        ? new Date(m.sk.split("#")[1]).toLocaleString()
        : "-");

    body.innerHTML += `
<tr class="${m.status === "READ" ? "mention-read" : "mention-unread"}">
  <td><strong>${m.mentionedBy || "Someone"}</strong></td>
  <td>
   <div class="mention-task"
     onclick="openTaskFromMention('${m.taskId}', '${m.sk}')">
      ${m.taskTitle || "Untitled Task"}
    </div>
    <div class="mention-sub">mentioned you</div>
  </td>
  <td>${m.comment || ""}</td>
  <td class="mention-date">${date}</td>
  <td>
    ${
      m.status === "READ"
        ? "✓ Read"
        : `<button class="btn btn-blue" onclick="markRead('${m.sk}')">Mark Read</button>`
    }
  </td>
</tr>`;
  });
}

async function markRead(sk) {
  await fetch(`${API}/mentions/${encodeURIComponent(sk)}`, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${token}` }
  });
  loadMentions();
}

function openTaskFromMention(taskId, sk) {
  // 1️⃣ Mark mention as READ (non-blocking)
  if (sk) {
    markRead(sk);
  }

  // 2️⃣ Switch to tasks view
  showView("tasks");

  // 3️⃣ Wait for tasks to render
  setTimeout(() => {
    const row = document.getElementById(`c-${taskId}`);
    if (!row) return;

    // Scroll task into view
    row.scrollIntoView({ behavior: "smooth", block: "center" });

    // Open comments if closed
    if (row.classList.contains("hidden")) {
      loadComments(taskId);
    }
  }, 600);
}

/* =========================================================
   AUDIT (UNCHANGED)
========================================================= */
async function loadAudit() {
  if (!isAdmin) return;

  const res = await fetch(`${API}/audit`, {
    headers: { Authorization: `Bearer ${token}` }
  });

  const logs = await res.json();
  logs.sort((a, b) =>
    new Date(b.updatedAt || b.createdAt) -
    new Date(a.updatedAt || a.createdAt)
  );

  const body = document.getElementById("auditBody");
  body.innerHTML = "";

  logs.forEach((l, i) => {
    body.innerHTML += `
<tr>
  <td>${i + 1}</td>
 <td>
  ${l.action}

  ${
    l.oldValue && l.newValue
      ? `<div style="font-size:12px;color:#94a3b8;margin-top:4px;">
           ${l.oldValue} → ${l.newValue}
         </div>`
      : ""
  }
</td>
  <td>${l.taskTitle || "-"}</td>
  <td>${l.updatedBy || l.createdBy || l.user || l.deletedBy || "-"}</td>
  <td>${formatLocalTime(l.updatedAt || l.createdAt)}</td>
</tr>`;
  });
}

/* =========================================================
   MENTIONS BADGE REFRESH
========================================================= */
async function refreshMentionsBadge() {
  const res = await fetch(`${API}/mentions`, {
    headers: { Authorization: `Bearer ${token}` }
  });

  const mentions = await res.json();
  const unread = mentions.filter(m => m.status !== "READ").length;

  const badge = document.getElementById("mentionsBadge");
  if (!badge) return;

  badge.style.display = unread ? "inline-block" : "none";
  badge.innerText = unread || "";
}

function handleTaskMentionInput(e) {
  // OPTIONAL AUTOCOMPLETE — SAFE NO-OP FOR NOW
  // Backend already handles mentions from text
  return;
}
/* =========================
   THEME HANDLING
========================= */
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("theme", theme);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "dark";
  applyTheme(current === "dark" ? "light" : "dark");
}

/* Init theme */
const savedTheme = localStorage.getItem("theme") || "dark";
applyTheme(savedTheme);

function sanitizeInput(text) {
  return text
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

/* =========================================================
   LOGOUT
========================================================= */
function logout() {
  localStorage.clear();
  location.href = "login.html";
}

/* Initial load */
showView("tasks");