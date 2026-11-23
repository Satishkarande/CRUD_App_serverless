/* FlowTasks frontend — API contracts match the uploaded Lambda route definitions. */
const API_BASE = "https://rq067no9cb.execute-api.ap-south-1.amazonaws.com";
const accessToken = localStorage.getItem("access_token");
const idToken = localStorage.getItem("id_token");
if (!accessToken) location.replace("login.html");

function decodeJwt(token) {
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(decodeURIComponent(escape(atob(payload))));
  } catch { return {}; }
}
const idClaims = decodeJwt(idToken || "");
const accessClaims = decodeJwt(accessToken || "");
const claims = Object.keys(idClaims).length ? idClaims : accessClaims;
const currentUserId = claims.sub || "";
const currentUsername = claims["cognito:username"] || claims.username || claims.email || "User";
const groupsClaim = claims["cognito:groups"] || [];
const groups = Array.isArray(groupsClaim) ? groupsClaim : (() => { try { return JSON.parse(groupsClaim); } catch { return [groupsClaim]; } })();
let isAdmin = groups.includes("admin") || (Array.isArray(accessClaims["cognito:groups"]) && accessClaims["cognito:groups"].includes("admin"));

const state = { tasks: [], mentions: [], audit: [], users: [], userNames: new Map(), view: "overview", mode: "table", activeTask: null, mentionQuery: "", dashboardFilter: null };
const $ = (id) => document.getElementById(id);
const esc = (value="") => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const date = value => { if (!value) return "—"; const d = new Date(value); return Number.isNaN(d.getTime()) ? "—" : new Intl.DateTimeFormat(undefined,{month:"short",day:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"}).format(d); };
// A task is overdue only when it has a valid due date, is not completed,
// and its due date is earlier than the start of today (local browser time).
function isOverdue(task) {
  const rawDueDate = task.dueDate || task.dueAt || task.deadline || task.due_date;
  if (!rawDueDate || task.status === "done") return false;
  const due = new Date(rawDueDate);
  if (Number.isNaN(due.getTime())) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  due.setHours(0, 0, 0, 0);
  return due < today;
}
const statusLabel = s => ({todo:"To do","in-progress":"In progress",done:"Done"}[s] || s || "Unknown");
const statusPill = s => `<span class="pill status-${esc(s)}"><i class="dot dot-${s==="done"?"green":s==="in-progress"?"amber":"blue"}"></i>${esc(statusLabel(s))}</span>`;
const priorityPill = p => `<span class="pill priority-${esc(p)}">${esc((p||"medium").toUpperCase())}</span>`;
const categoryPill = c => `<span class="pill category">${esc(c||"general")}</span>`;
function toast(message, error=false) { const el=document.createElement("div"); el.className="toast"+(error?" error":""); el.textContent=message; $("toastRegion").append(el); setTimeout(()=>el.remove(),3800); }
async function api(path, options={}) {
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers: { Authorization:`Bearer ${accessToken}`, ...(options.body?{"Content-Type":"application/json"}:{}), ...(options.headers||{}) } });
  const raw = await response.text(); let data; try { data=raw?JSON.parse(raw):{}; } catch { data=raw; }
  if (response.status===401) { toast("Your session has expired. Please sign in again.",true); setTimeout(()=>handleLogout(),900); throw new Error("Unauthorized"); }
  if (!response.ok) { const msg = data?.message || data?.error || `Request failed (${response.status})`; throw new Error(msg); }
  return data;
}
function handleLogout(){
  if (typeof window.cognitoLogout === "function") {
    window.cognitoLogout();
    return;
  }
  localStorage.removeItem("access_token");
  localStorage.removeItem("id_token");
  localStorage.removeItem("refresh_token");
  sessionStorage.clear();
  window.location.replace("login.html");
}
function setView(view) {
  state.view=view;
  document.querySelectorAll(".view").forEach(v=>v.classList.toggle("active",v.id===`view-${view}`));
  document.querySelectorAll(".nav-link").forEach(b=>b.classList.toggle("active",b.dataset.view===view && !b.dataset.dashboardFilter || b.dataset.dashboardFilter && view==="tasks" && state.dashboardFilter===b.dataset.dashboardFilter));
  $("crumbCurrent").textContent=view.toUpperCase();
  if(view==="overview") renderOverview();
  if(view==="tasks") renderTasks();
  if(view==="mentions") loadMentions();
  if(view==="inbox") { /* Messaging APIs are introduced in Phase 5. */ }
  if(view==="audit") loadAudit();
  $("sidebar").classList.remove("open");
}
function visibleTasks(){ return state.tasks; }
function filteredTasks(){
  const q=($("taskSearch")?.value||"").trim().toLowerCase(), cat=$("filterCategory")?.value||"", st=$("filterStatus")?.value||"", pr=$("filterPriority")?.value||"";
  return state.tasks.filter(t=>(!q||`${t.title||""} ${t.description||""} ${t.ownerName||""}`.toLowerCase().includes(q))&&(!cat||t.category===cat)&&(!st||t.status===st)&&(!pr||t.priority===pr)&&(!state.dashboardFilter||(
    state.dashboardFilter==="all" ||
    (state.dashboardFilter==="overdue" && isOverdue(t)) ||
    (["todo","in-progress","done"].includes(state.dashboardFilter) && t.status===state.dashboardFilter) ||
    (["high","medium","low"].includes(state.dashboardFilter) && t.priority===state.dashboardFilter)
  )));
}
async function loadTasks(){
  try {
    const result=await api("/tasks");
    state.tasks=Array.isArray(result)?result:[];
    state.tasks.sort((a,b)=>new Date(b.updatedAt||b.createdAt||0)-new Date(a.updatedAt||a.createdAt||0));
    $("navTaskCount").textContent=state.tasks.length;
    if($("navOverdueCount")) $("navOverdueCount").textContent=state.tasks.filter(isOverdue).length;
    renderOverview(); if(state.view==="tasks")renderTasks();
    return state.tasks;
  } catch(e){ toast(`Could not load tasks: ${e.message}`,true); throw e; }
}
function renderOverview(){
  const tasks=visibleTasks(), total=tasks.length, todo=tasks.filter(t=>t.status==="todo").length, progress=tasks.filter(t=>t.status==="in-progress").length, done=tasks.filter(t=>t.status==="done").length, high=tasks.filter(t=>t.priority==="high").length, overdue=tasks.filter(isOverdue).length;
  $("statTotal").textContent=total; $("statTodo").textContent=todo; $("statProgress").textContent=progress; $("statDone").textContent=done; $("statHigh").textContent=high; $("statOverdue").textContent=overdue;
  const rate=total?Math.round(done/total*100):0; $("completionRate").textContent=`${rate}%`; $("completionBar").style.width=`${rate}%`;
  $("priorityTotal").textContent=total;
  const max=Math.max(1,todo,progress,done);
  $("statusChart").innerHTML=[["TO DO",todo,"todo","todo"],["IN PROGRESS",progress,"progress","in-progress"],["DONE",done,"done","done"]].map(([label,n,cls,filter])=>`<button type="button" class="chart-col chart-filter" data-dashboard-filter="${filter}" aria-label="Filter ${label} tasks"><span class="bar-stack"><i class="${cls}" style="height:${n/max*100}%"></i></span><small>${label}<br>${n}</small></button>`).join("");
  const low=tasks.filter(t=>t.priority==="low").length, med=tasks.filter(t=>t.priority==="medium").length;
  const hiPct=total?high/total*100:0, medPct=total?med/total*100:0;
  $("priorityRing").style.background=`conic-gradient(var(--red) 0 ${hiPct}%,var(--amber) ${hiPct}% ${hiPct+medPct}%,var(--green) ${hiPct+medPct}% 100%)`;
  $("priorityLegend").innerHTML=`<button type="button" data-dashboard-filter="high">HIGH<b>${high}</b></button><button type="button" data-dashboard-filter="medium">MEDIUM<b>${med}</b></button><button type="button" data-dashboard-filter="low">LOW<b>${low}</b></button>`;
  $("recentTasks").innerHTML=tasks.slice(0,5).map(t=>`<tr><td><div class="task-name">${esc(t.title)}</div><div class="task-desc">${esc(t.description||"No description")}</div></td><td>${statusPill(t.status)}</td><td>${priorityPill(t.priority)}</td><td>${date(t.updatedAt||t.createdAt)}</td><td><button class="mini-btn" data-detail="${esc(t.taskId||t.id)}">Open ↗</button></td></tr>`).join("")||`<tr><td colspan="5" class="empty-state">No tasks yet. Create your first task.</td></tr>`;
}
function taskRow(t){
 return `<tr><td><div class="task-name">${esc(t.title)}</div><div class="task-desc">${esc(t.description||"No description")}</div></td><td>${categoryPill(t.category)}</td><td>${statusPill(t.status)}</td><td>${priorityPill(t.priority)}</td><td>${esc(displayUser(t.ownerId)||displayUser(t.createdBy)||displayUser(t.ownerName))}${t.ownerId===currentUserId?' <span class="cyan-text">(You)</span>':""}</td><td><button class="mini-btn" data-detail="${esc(t.taskId||t.id)}">View ${Number(t.commentCount||0)} comments</button></td><td>${date(t.updatedAt||t.createdAt)}</td><td><div class="row-actions"><button class="mini-btn" data-detail="${esc(t.taskId||t.id)}">Details</button><button class="mini-btn" data-delete-task="${esc(t.taskId||t.id)}">Delete</button></div></td></tr>`;
}
function renderTasks(){
 const tasks=filteredTasks(); $("taskRows").innerHTML=tasks.map(taskRow).join(""); $("taskEmpty").classList.toggle("hidden",tasks.length>0);
 const columns=[["todo","todoCards","todoCount"],["in-progress","progressCards","progressCount"],["done","doneCards","doneCount"]];
 for(const [status,container,count] of columns){const list=tasks.filter(t=>t.status===status);$(count).textContent=list.length;$(container).innerHTML=list.map(t=>`<article class="kanban-card" draggable="true" data-drag-task="${esc(t.taskId||t.id)}"><h3>${esc(t.title)}</h3><p>${esc(t.description||"No description")}</p><div class="kanban-card-foot">${priorityPill(t.priority)}<small>${esc(displayUser(t.ownerId)||displayUser(t.ownerName)||displayUser(t.createdBy)||"")}</small></div><div class="row-actions" style="margin-top:10px"><button class="mini-btn" data-detail="${esc(t.taskId||t.id)}">Details</button></div></article>`).join("")||`<div class="empty-state">No tasks</div>`;}
 $("taskTableView").classList.toggle("hidden",state.mode!=="table"); $("kanbanView").classList.toggle("hidden",state.mode!=="board");
}
function openTaskModal(){ $("taskForm").reset(); $("taskPriority").value="medium"; $("taskStatus").value="todo"; $("taskModalTitle").textContent="Create task"; $("saveTaskBtn").textContent="Create task ↗"; $("taskModal").classList.remove("hidden"); $("taskTitle").focus(); }
function closeModal(id){$(id).classList.add("hidden");}
async function createTask(e){
 e.preventDefault(); const payload={title:$("taskTitle").value.trim(),description:$("taskDescription").value.trim(),category:$("taskCategory").value,status:$("taskStatus").value,priority:$("taskPriority").value};
 if(!payload.title)return;
 const btn=$("saveTaskBtn");btn.disabled=true;btn.textContent="Creating…";
 try{await api("/tasks",{method:"POST",body:JSON.stringify(payload)});closeModal("taskModal");toast("Task created successfully.");await loadTasks();}
 catch(err){toast(`Task creation failed: ${err.message}`,true);}
 finally{btn.disabled=false;btn.textContent="Create task ↗";}
}
function getTask(id){return state.tasks.find(t=>(t.taskId||t.id)===id);}
function displayUser(idOrName){
 if(!idOrName) return "";
 const value = String(idOrName);
 const mapped = state.userNames.get(value);
 if (mapped) return mapped;
 // Do not expose Cognito sub UUIDs in the UI if the directory lookup is unavailable.
 if (/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)) return "User";
 return value;
}
async function updateTaskField(id,field,value){
 const task=getTask(id);if(!task)return;
 if(!["status","priority"].includes(field)){toast("This field is not editable through the current backend API.",true);return;}
 try{await api(`/tasks/${encodeURIComponent(id)}`,{method:"PUT",body:JSON.stringify({[field]:value})});toast(`${field==="status"?"Status":"Priority"} updated.`);await loadTasks();}
 catch(e){toast(`Update failed: ${e.message}`,true);renderTasks();}
}
async function deleteTask(id){
 const task=getTask(id);if(!task)return;
 if(!confirm(`Delete "${task.title}"? This will soft-delete the task.`))return;
 try{await api(`/tasks/${encodeURIComponent(id)}`,{method:"DELETE"});toast("Task deleted.");await loadTasks();}
 catch(e){toast(`Delete failed: ${e.message}`,true);}
}
async function openTaskDetails(id){
 const task=getTask(id);if(!task)return;state.activeTask=task;
 $("detailTitle").textContent=task.title||"Task details";
 $("detailContent").innerHTML=`<div class="detail-meta">${statusPill(task.status)} ${priorityPill(task.priority)} ${categoryPill(task.category)}<span class="pill category">OWNER: ${esc(displayUser(task.ownerId)||displayUser(task.ownerName)||"—")}</span></div><div class="form-grid"><label>Task title<input id="detailTaskTitle" maxlength="150"></label><label>Category<select id="detailCategory"><option value="general">General</option><option value="work">Work</option><option value="personal">Personal</option><option value="bug">Bug</option><option value="feature">Feature</option><option value="ops">Operations</option></select></label><label>Update status<select id="detailStatus"><option value="todo">To do</option><option value="in-progress">In progress</option><option value="done">Done</option></select></label><label>Update priority<select id="detailPriority"><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label><label style="grid-column:1/-1">Description<textarea id="detailDescription" maxlength="5000" rows="3"></textarea></label><div><div class="detail-section-title">CREATED</div><div class="comment-date">${date(task.createdAt)}</div></div></div><div class="modal-actions"><button class="secondary-btn" id="saveTaskMeta">Save task changes</button></div><div class="detail-section-title">COMMENTS & DISCUSSION</div><div id="commentList">Loading comments…</div><div class="comment-compose mention-compose"><div class="mention-input-wrap"><input id="newComment" maxlength="5000" autocomplete="off" placeholder="Write a comment… type @ to mention a teammate"><div id="commentMentionSuggestions" class="suggestions hidden"></div></div><button class="primary-btn" id="sendComment">Send ↗</button></div>`;
 $("detailTaskTitle").value=task.title||""; $("detailDescription").value=task.description||""; $("detailCategory").value=task.category||"general"; $("detailStatus").value=task.status; $("detailPriority").value=task.priority; $("detailModal").classList.remove("hidden");
 $("saveTaskMeta").onclick=async()=>{
   const payload={title:$("detailTaskTitle").value.trim(),description:$("detailDescription").value.trim(),category:$("detailCategory").value,status:$("detailStatus").value,priority:$("detailPriority").value};
   const btn=$("saveTaskMeta");btn.disabled=true;
   try{await api(`/tasks/${encodeURIComponent(id)}`,{method:"PUT",body:JSON.stringify(payload)});toast("Task details updated.");await loadTasks();state.activeTask=getTask(id);$("detailTitle").textContent=state.activeTask.title;}
   catch(e){toast(`Task update failed: ${e.message}`,true);}
   finally{btn.disabled=false;}
 };
 $("sendComment").onclick=()=>addComment(id);
 await loadComments(id);
}
async function loadComments(taskId){
 const list=$("commentList");if(!list)return;
 try{const comments=await api(`/tasks/${encodeURIComponent(taskId)}/comments`);comments.sort((a,b)=>new Date(a.createdAt||0)-new Date(b.createdAt||0));
 list.innerHTML=comments.map(c=>`<div class="comment-item"><div class="comment-top"><strong>${esc(displayUser(c.userId)||c.userName||"User")}</strong><span class="comment-date">${date(c.createdAt)}</span></div><div class="comment-body">${esc(c.comment||"")}</div><div class="row-actions">${(isAdmin||c.userId===currentUserId)?`<button class="mini-btn" data-edit-comment="${esc(c.commentId)}">Edit</button><button class="mini-btn" data-delete-comment="${esc(c.commentId)}">Delete</button>`:""}</div></div>`).join("")||`<div class="empty-state">No comments yet. Start the conversation.</div>`;
 }catch(e){list.innerHTML=`<div class="empty-state">Could not load comments: ${esc(e.message)}</div>`;}
}
async function addComment(taskId){
 const input=$("newComment"),comment=input.value.trim();if(!comment)return;
 try{await api(`/tasks/${encodeURIComponent(taskId)}/comments`,{method:"POST",body:JSON.stringify({comment})});input.value="";toast("Comment added.");await loadComments(taskId);await loadTasks();}
 catch(e){toast(`Could not add comment: ${e.message}`,true);}
}
async function editComment(taskId,commentId){
 const comments=await api(`/tasks/${encodeURIComponent(taskId)}/comments`);const c=comments.find(x=>x.commentId===commentId);if(!c)return;
 const updated=prompt("Edit your comment:",c.comment||"");if(updated===null||!updated.trim()||updated.trim()===c.comment)return;
 try{await api(`/tasks/${encodeURIComponent(taskId)}/comments/${encodeURIComponent(commentId)}`,{method:"PUT",body:JSON.stringify({comment:updated.trim()})});toast("Comment updated.");await loadComments(taskId);}
 catch(e){toast(`Comment edit failed: ${e.message}`,true);}
}
async function deleteComment(taskId,commentId){
 if(!confirm("Delete this comment?"))return;
 try{await api(`/tasks/${encodeURIComponent(taskId)}/comments/${encodeURIComponent(commentId)}`,{method:"DELETE"});toast("Comment deleted.");await loadComments(taskId);await loadTasks();}
 catch(e){toast(`Comment deletion failed: ${e.message}. Check the deployed DeleteComment Lambda package.`,true);}
}
async function loadUsers(){
 try {
   state.users = await api("/users");
   state.userNames = new Map();
   for (const user of state.users) {
     const label = user.displayName || user.username || user.name ||
       (user.email && user.email.includes("@") ? user.email.split("@")[0] : "User");
     if (user.sub) state.userNames.set(user.sub, label);
     if (user.username) state.userNames.set(user.username, label);
     if (user.cognitoUsername) state.userNames.set(user.cognitoUsername, label);
     if (user.email) state.userNames.set(user.email, label);
   }

   const current = state.users.find(u => u.sub === currentUserId);
   const currentLabel = current
     ? (current.displayName || current.username || current.name ||
        (current.email && current.email.includes("@") ? current.email.split("@")[0] : "User"))
     : (claims.email && claims.email.includes("@") ? claims.email.split("@")[0] :
        (currentUsername !== currentUserId ? currentUsername : "User"));
   $("userName").textContent = currentLabel;
   $("userAvatar").textContent = (currentLabel[0] || "U").toUpperCase();

   // The audit endpoint is also the authoritative server-side admin check.
   try {
     await api("/audit");
     isAdmin = true;
     $("userRole").textContent = "ADMINISTRATOR";
     $("auditNav").classList.remove("hidden");
 if($("inboxCount")) $("inboxCount").textContent="0";
   } catch (authorizationError) {
     isAdmin = false;
     $("userRole").textContent = "MEMBER";
     $("auditNav").classList.add("hidden");
   }
 } catch(e) {
   console.warn("User directory unavailable; display names cannot be resolved", e);
   const fallback = claims.email && claims.email.includes("@") ? claims.email.split("@")[0] :
     (currentUsername !== currentUserId ? currentUsername : "User");
   $("userName").textContent = fallback;
   $("userAvatar").textContent = (fallback[0] || "U").toUpperCase();
 }
}

async function loadMentions(){
 try{
   state.mentions=await api("/mentions");
   const unread=state.mentions.filter(m=>m.status!=="READ").length;
   $("mentionCount").textContent=unread;
   $("mentionRows").innerHTML=state.mentions.map(m=>{
     // New records carry mentionedById; historical records can be resolved
     // through the Cognito username map populated by GET /users.
     const sender=displayUser(m.mentionedById||m.mentionedBy)||"Teammate";
     const preview=m.comment||m.messagePreview||"Mentioned you in a task";
     return `<tr class="${m.status!=="READ"?"mention-unread":""}"><td>${esc(sender)}</td><td><button class="text-btn" data-open-mention="${esc(m.taskId)}">${esc(m.taskTitle||"Task")}</button></td><td>${esc(preview)}</td><td>${date(m.createdAt)}</td><td>${m.status==="READ"?'<span class="pill status-done">READ</span>':'<span class="pill status-in-progress">UNREAD</span>'}</td><td>${m.status==="READ"?"":`<button class="mini-btn" data-mark-read="${esc(m.sk)}">Mark read</button>`}</td></tr>`;
   }).join("");
   $("mentionEmpty").classList.toggle("hidden",state.mentions.length>0);
 }catch(e){toast(`Could not load mentions: ${e.message}`,true);}
}
async function markMentionRead(sk){
 try{await api(`/mentions/${encodeURIComponent(sk)}`,{method:"PATCH"});await loadMentions();toast("Mention marked as read.");}
 catch(e){toast(`Could not update mention: ${e.message}`,true);}
}
async function openMentionTask(taskId){
 try{
  await loadTasks();
  if(!getTask(taskId)){toast("This task is not available to your account yet. Refresh and try again.",true);return;}
  setView("tasks");
  await openTaskDetails(taskId);
  const mention=state.mentions.find(m=>m.taskId===taskId&&m.status!=="READ");
  if(mention) await markMentionRead(mention.sk);
 }catch(e){toast(`Could not open mentioned task: ${e.message}`,true);}
}
async function loadAudit(){
 if(!isAdmin){toast("Audit trail is available to admins only.",true);setView("overview");return;}
 try{state.audit=await api("/audit");$("auditTotal").textContent=state.audit.length;$("auditRows").innerHTML=state.audit.map(a=>`<tr><td><span class="pill category">${esc(a.action||"EVENT")}</span></td><td>${esc(a.taskTitle||a.taskId||"—")}</td><td>${esc(a.newValue!==undefined?`${a.oldValue??"—"} → ${a.newValue}`:a.deletedComment||"—")}</td><td>${esc(displayUser(a.actorId)||displayUser(a.userId)||displayUser(a.updatedById)||a.actorName||displayUser(a.updatedBy)||displayUser(a.deletedBy)||displayUser(a.createdBy)||"—")}</td><td>${date(a.updatedAt||a.createdAt||a.deletedAt)}</td></tr>`).join("");$("auditEmpty").classList.toggle("hidden",state.audit.length>0);}
 catch(e){toast(`Could not load audit trail: ${e.message}`,true);}
}
function attachMentionAutocomplete(inputId, boxId){
 const input=$(inputId), box=$(boxId);
 if(!input || !box) return;
 input.addEventListener("input",()=>{
  const cursor=input.selectionStart ?? input.value.length;
  const before=input.value.slice(0,cursor);
  const match=before.match(/(?:^|\s)@([^@\n]*)$/);
  if(!match){box.classList.add("hidden");return;}
  const query=match[1].trim().toLowerCase();
  const matches=state.users.filter(user=>{
   const label=user.displayName||user.username||user.name||"";
   return label && label.toLowerCase().includes(query);
  }).slice(0,7);
  box.innerHTML=matches.map((user,index)=>{
   const label=user.displayName||user.username||user.name||"User";
   const secondary=user.email||user.cognitoUsername||"";
   return `<button type="button" data-mention-index="${index}"><strong>${esc(label)}</strong>${secondary?` <small>${esc(secondary)}</small>`:""}</button>`;
  }).join("");
  box._mentionMatches=matches;
  box.classList.toggle("hidden",matches.length===0);
 });
 box.addEventListener("mousedown",event=>event.preventDefault());
 box.addEventListener("click",event=>{
  const button=event.target.closest("[data-mention-index]");
  if(!button) return;
  const user=box._mentionMatches?.[Number(button.dataset.mentionIndex)];
  if(!user) return;
  const label=user.displayName||user.username||user.name||"User";
  const cursor=input.selectionStart ?? input.value.length;
  const before=input.value.slice(0,cursor);
  const match=before.match(/(?:^|\s)@([^@\n]*)$/);
  if(!match) return;
  const start=before.length-match[0].length+(match[0].startsWith(" ")?1:0);
  input.setRangeText(`@${label} `,start,cursor,"end");
  box.classList.add("hidden");
  input.focus();
 });
 input.addEventListener("keydown",event=>{if(event.key==="Escape")box.classList.add("hidden");});
 document.addEventListener("click",event=>{if(!box.contains(event.target)&&event.target!==input)box.classList.add("hidden");});
}

function applyDashboardFilter(filter){
 state.dashboardFilter=filter||null;
 $("taskSearch").value="";
 $("filterCategory").value="";
 $("filterStatus").value="";
 $("filterPriority").value="";
 setView("tasks");
 renderTasks();
 const labels={all:"all accessible tasks",todo:"to do tasks","in-progress":"in progress tasks",done:"completed tasks",high:"high priority tasks",medium:"medium priority tasks",low:"low priority tasks",overdue:"overdue tasks"};
 if($("taskPageTitle")) { $("taskPageTitle").innerHTML=filter==="overdue"?"Overdue <em>tasks</em>":"Task <em>board</em>"; $("taskPageDescription").textContent=filter==="overdue"?"Tasks past their due date that are not completed.":"Track work from first action to completion."; }
 document.querySelectorAll(".nav-link").forEach(b=>b.classList.toggle("active",b.dataset.view==="tasks" && !filter || b.dataset.dashboardFilter===filter));
 toast(`Showing ${labels[filter]||"filtered tasks"}.`);
}
function setupEvents(){
 document.querySelectorAll("[data-view]").forEach(b=>b.addEventListener("click",()=>{if(b.dataset.view==="tasks"){state.dashboardFilter=null;if($("taskPageTitle")){$("taskPageTitle").innerHTML="Task <em>board</em>";$("taskPageDescription").textContent="Track work from first action to completion.";}}setView(b.dataset.view);}));
 ["topCreateBtn","heroCreateBtn","createTaskBtn"].forEach(id=>$(id).addEventListener("click",openTaskModal));
 $("taskForm").addEventListener("submit",createTask);
 document.querySelectorAll("[data-close]").forEach(b=>b.addEventListener("click",()=>closeModal(b.dataset.close)));
 ["taskSearch","filterCategory","filterStatus","filterPriority"].forEach(id=>$(id).addEventListener(id==="taskSearch"?"input":"change",()=>{state.dashboardFilter=null;if($("taskPageTitle")){$("taskPageTitle").innerHTML="Task <em>board</em>";$("taskPageDescription").textContent="Track work from first action to completion.";}document.querySelectorAll(".nav-link").forEach(b=>b.classList.toggle("active",b.dataset.view==="tasks"));renderTasks();}));
 $("tableModeBtn").onclick=()=>{state.mode="table";$("tableModeBtn").classList.add("selected");$("boardModeBtn").classList.remove("selected");renderTasks();};
 $("boardModeBtn").onclick=()=>{state.mode="board";$("boardModeBtn").classList.add("selected");$("tableModeBtn").classList.remove("selected");renderTasks();};
 $("refreshBtn").onclick=async()=>{await loadTasks();if(state.view==="mentions")await loadMentions();toast("Workspace refreshed.");};
 $("refreshMentionsBtn").onclick=loadMentions;$("refreshAuditBtn").onclick=loadAudit;
 $("logoutBtn").onclick=handleLogout;$("mobileMenu").onclick=()=>$("sidebar").classList.toggle("open");
 document.querySelectorAll("[data-quick-status]").forEach(b=>b.onclick=()=>{setView("tasks");$("filterStatus").value=b.dataset.quickStatus;renderTasks();});
 document.addEventListener("click",e=>{
  const dashboardFilter=e.target.closest("[data-dashboard-filter]");
  if(dashboardFilter){applyDashboardFilter(dashboardFilter.dataset.dashboardFilter);return;}
  const d=e.target.closest("[data-detail]");if(d)openTaskDetails(d.dataset.detail);
  const del=e.target.closest("[data-delete-task]");if(del)deleteTask(del.dataset.deleteTask);
  const mr=e.target.closest("[data-mark-read]");if(mr)markMentionRead(mr.dataset.markRead);
  const om=e.target.closest("[data-open-mention]");if(om){openMentionTask(om.dataset.openMention);}
  const ec=e.target.closest("[data-edit-comment]");if(ec&&state.activeTask)editComment(state.activeTask.taskId||state.activeTask.id,ec.dataset.editComment);
  const dc=e.target.closest("[data-delete-comment]");if(dc&&state.activeTask)deleteComment(state.activeTask.taskId||state.activeTask.id,dc.dataset.deleteComment);
 });
 document.querySelectorAll("[data-drop-status]").forEach(col=>{
  col.addEventListener("dragover",e=>{e.preventDefault();col.closest(".kanban-column").classList.add("drag-over");});
  col.addEventListener("dragleave",()=>col.closest(".kanban-column").classList.remove("drag-over"));
  col.addEventListener("drop",e=>{e.preventDefault();col.closest(".kanban-column").classList.remove("drag-over");const id=e.dataTransfer.getData("text/plain");if(id)updateTaskField(id,"status",col.dataset.dropStatus);});
 });
 document.addEventListener("keydown",e=>{
  if((e.key==="Enter"||e.key===" ") && e.target.matches(".dashboard-card[data-dashboard-filter]")){
   e.preventDefault();applyDashboardFilter(e.target.dataset.dashboardFilter);
  }
 });
 document.addEventListener("dragstart",e=>{const card=e.target.closest("[data-drag-task]");if(card)e.dataTransfer.setData("text/plain",card.dataset.dragTask);});
 attachMentionAutocomplete("taskDescription", "mentionSuggestions");
 attachMentionAutocomplete("newComment", "commentMentionSuggestions");
}
function init(){
 const initialLabel = currentUsername !== currentUserId ? currentUsername :
   (claims.email && claims.email.includes("@") ? claims.email.split("@")[0] : "User");
 $("userName").textContent=initialLabel;$("userAvatar").textContent=(initialLabel[0]||"U").toUpperCase();$("userRole").textContent=isAdmin?"ADMINISTRATOR":"MEMBER";
 $("auditNav").classList.remove("hidden");
 if($("inboxCount")) $("inboxCount").textContent="0";
 $("todayLabel").textContent=new Intl.DateTimeFormat(undefined,{weekday:"long",month:"short",day:"2-digit"}).format(new Date()).toUpperCase();
 setupEvents();(async()=>{await loadUsers();await loadTasks();await loadMentions();})().catch(()=>{});
}
init();
