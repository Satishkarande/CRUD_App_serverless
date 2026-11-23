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
const claims = decodeJwt(idToken || accessToken);
const currentUserId = claims.sub || "";
const currentUsername = claims["cognito:username"] || claims.username || claims.email || "User";
const groupsClaim = claims["cognito:groups"] || [];
const groups = Array.isArray(groupsClaim) ? groupsClaim : (() => { try { return JSON.parse(groupsClaim); } catch { return [groupsClaim]; } })();
const isAdmin = groups.includes("admin");

const state = { tasks: [], mentions: [], audit: [], users: [], view: "overview", mode: "table", activeTask: null, mentionQuery: "" };
const $ = (id) => document.getElementById(id);
const esc = (value="") => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const date = value => { if (!value) return "—"; const d = new Date(value); return Number.isNaN(d.getTime()) ? "—" : new Intl.DateTimeFormat(undefined,{month:"short",day:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"}).format(d); };
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
  document.querySelectorAll(".nav-link").forEach(b=>b.classList.toggle("active",b.dataset.view===view));
  $("crumbCurrent").textContent=view.toUpperCase();
  if(view==="overview") renderOverview();
  if(view==="tasks") renderTasks();
  if(view==="mentions") loadMentions();
  if(view==="audit") loadAudit();
  $("sidebar").classList.remove("open");
}
function visibleTasks(){ return state.tasks; }
function filteredTasks(){
  const q=($("taskSearch")?.value||"").trim().toLowerCase(), cat=$("filterCategory")?.value||"", st=$("filterStatus")?.value||"", pr=$("filterPriority")?.value||"";
  return state.tasks.filter(t=>(!q||`${t.title||""} ${t.description||""} ${t.ownerName||""}`.toLowerCase().includes(q))&&(!cat||t.category===cat)&&(!st||t.status===st)&&(!pr||t.priority===pr));
}
async function loadTasks(){
  try {
    const result=await api("/tasks");
    state.tasks=Array.isArray(result)?result:[];
    state.tasks.sort((a,b)=>new Date(b.updatedAt||b.createdAt||0)-new Date(a.updatedAt||a.createdAt||0));
    $("navTaskCount").textContent=state.tasks.length;
    renderOverview(); if(state.view==="tasks")renderTasks();
    return state.tasks;
  } catch(e){ toast(`Could not load tasks: ${e.message}`,true); throw e; }
}
function renderOverview(){
  const tasks=visibleTasks(), total=tasks.length, todo=tasks.filter(t=>t.status==="todo").length, progress=tasks.filter(t=>t.status==="in-progress").length, done=tasks.filter(t=>t.status==="done").length, high=tasks.filter(t=>t.priority==="high").length;
  $("statTotal").textContent=total; $("statProgress").textContent=progress; $("statDone").textContent=done; $("statHigh").textContent=high;
  const rate=total?Math.round(done/total*100):0; $("completionRate").textContent=`${rate}%`; $("completionBar").style.width=`${rate}%`;
  $("priorityTotal").textContent=total;
  const max=Math.max(1,todo,progress,done);
  $("statusChart").innerHTML=[["TO DO",todo,"todo"],["IN PROGRESS",progress,"progress"],["DONE",done,"done"]].map(([label,n,cls])=>`<div class="chart-col"><div class="bar-stack"><i class="${cls}" style="height:${n/max*100}%"></i></div><small>${label}<br>${n}</small></div>`).join("");
  const low=tasks.filter(t=>t.priority==="low").length, med=tasks.filter(t=>t.priority==="medium").length;
  const hiPct=total?high/total*100:0, medPct=total?med/total*100:0;
  $("priorityRing").style.background=`conic-gradient(var(--red) 0 ${hiPct}%,var(--amber) ${hiPct}% ${hiPct+medPct}%,var(--green) ${hiPct+medPct}% 100%)`;
  $("priorityLegend").innerHTML=`<div>HIGH<b>${high}</b></div><div>MEDIUM<b>${med}</b></div><div>LOW<b>${low}</b></div>`;
  $("recentTasks").innerHTML=tasks.slice(0,5).map(t=>`<tr><td><div class="task-name">${esc(t.title)}</div><div class="task-desc">${esc(t.description||"No description")}</div></td><td>${statusPill(t.status)}</td><td>${priorityPill(t.priority)}</td><td>${date(t.updatedAt||t.createdAt)}</td><td><button class="mini-btn" data-detail="${esc(t.taskId||t.id)}">Open ↗</button></td></tr>`).join("")||`<tr><td colspan="5" class="empty-state">No tasks yet. Create your first task.</td></tr>`;
}
function taskRow(t){
 return `<tr><td><div class="task-name">${esc(t.title)}</div><div class="task-desc">${esc(t.description||"No description")}</div></td><td>${categoryPill(t.category)}</td><td>${statusPill(t.status)}</td><td>${priorityPill(t.priority)}</td><td>${esc(t.ownerName||t.createdBy||"—")}${t.ownerId===currentUserId?' <span class="cyan-text">(You)</span>':""}</td><td>${date(t.updatedAt||t.createdAt)}</td><td><div class="row-actions"><button class="mini-btn" data-detail="${esc(t.taskId||t.id)}">Details</button><button class="mini-btn" data-delete-task="${esc(t.taskId||t.id)}">Delete</button></div></td></tr>`;
}
function renderTasks(){
 const tasks=filteredTasks(); $("taskRows").innerHTML=tasks.map(taskRow).join(""); $("taskEmpty").classList.toggle("hidden",tasks.length>0);
 const columns=[["todo","todoCards","todoCount"],["in-progress","progressCards","progressCount"],["done","doneCards","doneCount"]];
 for(const [status,container,count] of columns){const list=tasks.filter(t=>t.status===status);$(count).textContent=list.length;$(container).innerHTML=list.map(t=>`<article class="kanban-card" draggable="true" data-drag-task="${esc(t.taskId||t.id)}"><h3>${esc(t.title)}</h3><p>${esc(t.description||"No description")}</p><div class="kanban-card-foot">${priorityPill(t.priority)}<small>${esc(t.ownerName||"")}</small></div><div class="row-actions" style="margin-top:10px"><button class="mini-btn" data-detail="${esc(t.taskId||t.id)}">Details</button></div></article>`).join("")||`<div class="empty-state">No tasks</div>`;}
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
 $("detailContent").innerHTML=`<div class="detail-meta">${statusPill(task.status)} ${priorityPill(task.priority)} ${categoryPill(task.category)}<span class="pill category">OWNER: ${esc(task.ownerName||"—")}</span></div><div class="detail-description">${esc(task.description||"No description provided.")}</div><div class="form-grid"><label>Update status<select id="detailStatus"><option value="todo">To do</option><option value="in-progress">In progress</option><option value="done">Done</option></select></label><label>Update priority<select id="detailPriority"><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label><div><div class="detail-section-title">CREATED</div><div class="comment-date">${date(task.createdAt)}</div></div></div><div class="modal-actions"><button class="secondary-btn" id="saveTaskMeta">Save status & priority</button></div><div class="detail-section-title">COMMENTS & DISCUSSION</div><div id="commentList">Loading comments…</div><div class="comment-compose"><input id="newComment" maxlength="5000" placeholder="Write a comment… use @username to mention"><button class="primary-btn" id="sendComment">Send ↗</button></div>`;
 $("detailStatus").value=task.status; $("detailPriority").value=task.priority; $("detailModal").classList.remove("hidden");
 $("saveTaskMeta").onclick=async()=>{await updateTaskField(id,"status",$("detailStatus").value);await updateTaskField(id,"priority",$("detailPriority").value);state.activeTask=getTask(id);};
 $("sendComment").onclick=()=>addComment(id);
 await loadComments(id);
}
async function loadComments(taskId){
 const list=$("commentList");if(!list)return;
 try{const comments=await api(`/tasks/${encodeURIComponent(taskId)}/comments`);comments.sort((a,b)=>new Date(a.createdAt||0)-new Date(b.createdAt||0));
 list.innerHTML=comments.map(c=>`<div class="comment-item"><div class="comment-top"><strong>${esc(c.userName||"User")}</strong><span class="comment-date">${date(c.createdAt)}</span></div><div class="comment-body">${esc(c.comment||"")}</div><div class="row-actions">${(isAdmin||c.userId===currentUserId)?`<button class="mini-btn" data-edit-comment="${esc(c.commentId)}">Edit</button><button class="mini-btn" data-delete-comment="${esc(c.commentId)}">Delete</button>`:""}</div></div>`).join("")||`<div class="empty-state">No comments yet. Start the conversation.</div>`;
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
async function loadUsers(){try{state.users=await api("/users");}catch(e){console.warn("User directory unavailable",e);state.users=[];}}
async function loadMentions(){
 try{state.mentions=await api("/mentions");const unread=state.mentions.filter(m=>m.status!=="READ").length;$("mentionCount").textContent=unread;$("mentionRows").innerHTML=state.mentions.map(m=>`<tr class="${m.status!=="READ"?"mention-unread":""}"><td>${esc(m.mentionedBy||"Teammate")}</td><td><button class="text-btn" data-open-mention="${esc(m.taskId)}">${esc(m.taskTitle||"Task")}</button></td><td>${esc(m.comment||"Mentioned you")}</td><td>${date(m.createdAt)}</td><td>${m.status==="READ"?'<span class="pill status-done">READ</span>':'<span class="pill status-in-progress">UNREAD</span>'}</td><td>${m.status==="READ"?"":`<button class="mini-btn" data-mark-read="${esc(m.sk)}">Mark read</button>`}</td></tr>`).join("");$("mentionEmpty").classList.toggle("hidden",state.mentions.length>0);}
 catch(e){toast(`Could not load mentions: ${e.message}`,true);}
}
async function markMentionRead(sk){
 try{await api(`/mentions/${encodeURIComponent(sk)}`,{method:"PATCH"});await loadMentions();toast("Mention marked as read.");}
 catch(e){toast(`Could not update mention: ${e.message}`,true);}
}
async function loadAudit(){
 if(!isAdmin){toast("Audit trail is available to admins only.",true);setView("overview");return;}
 try{state.audit=await api("/audit");$("auditTotal").textContent=state.audit.length;$("auditRows").innerHTML=state.audit.map(a=>`<tr><td><span class="pill category">${esc(a.action||"EVENT")}</span></td><td>${esc(a.taskTitle||a.taskId||"—")}</td><td>${esc(a.newValue!==undefined?`${a.oldValue??"—"} → ${a.newValue}`:a.deletedComment||"—")}</td><td>${esc(a.actorName||a.updatedBy||a.deletedBy||a.createdBy||"—")}</td><td>${date(a.updatedAt||a.createdAt||a.deletedAt)}</td></tr>`).join("");$("auditEmpty").classList.toggle("hidden",state.audit.length>0);}
 catch(e){toast(`Could not load audit trail: ${e.message}`,true);}
}
function setupEvents(){
 document.querySelectorAll("[data-view]").forEach(b=>b.addEventListener("click",()=>setView(b.dataset.view)));
 ["topCreateBtn","heroCreateBtn","createTaskBtn"].forEach(id=>$(id).addEventListener("click",openTaskModal));
 $("taskForm").addEventListener("submit",createTask);
 document.querySelectorAll("[data-close]").forEach(b=>b.addEventListener("click",()=>closeModal(b.dataset.close)));
 ["taskSearch","filterCategory","filterStatus","filterPriority"].forEach(id=>$(id).addEventListener(id==="taskSearch"?"input":"change",renderTasks));
 $("tableModeBtn").onclick=()=>{state.mode="table";$("tableModeBtn").classList.add("selected");$("boardModeBtn").classList.remove("selected");renderTasks();};
 $("boardModeBtn").onclick=()=>{state.mode="board";$("boardModeBtn").classList.add("selected");$("tableModeBtn").classList.remove("selected");renderTasks();};
 $("refreshBtn").onclick=async()=>{await loadTasks();if(state.view==="mentions")await loadMentions();toast("Workspace refreshed.");};
 $("refreshMentionsBtn").onclick=loadMentions;$("refreshAuditBtn").onclick=loadAudit;
 $("logoutBtn").onclick=handleLogout;$("mobileMenu").onclick=()=>$("sidebar").classList.toggle("open");
 document.querySelectorAll("[data-quick-status]").forEach(b=>b.onclick=()=>{setView("tasks");$("filterStatus").value=b.dataset.quickStatus;renderTasks();});
 document.addEventListener("click",e=>{
  const d=e.target.closest("[data-detail]");if(d)openTaskDetails(d.dataset.detail);
  const del=e.target.closest("[data-delete-task]");if(del)deleteTask(del.dataset.deleteTask);
  const mr=e.target.closest("[data-mark-read]");if(mr)markMentionRead(mr.dataset.markRead);
  const om=e.target.closest("[data-open-mention]");if(om){setView("tasks");openTaskDetails(om.dataset.openMention);}
  const ec=e.target.closest("[data-edit-comment]");if(ec&&state.activeTask)editComment(state.activeTask.taskId||state.activeTask.id,ec.dataset.editComment);
  const dc=e.target.closest("[data-delete-comment]");if(dc&&state.activeTask)deleteComment(state.activeTask.taskId||state.activeTask.id,dc.dataset.deleteComment);
 });
 document.querySelectorAll("[data-drop-status]").forEach(col=>{
  col.addEventListener("dragover",e=>{e.preventDefault();col.closest(".kanban-column").classList.add("drag-over");});
  col.addEventListener("dragleave",()=>col.closest(".kanban-column").classList.remove("drag-over"));
  col.addEventListener("drop",e=>{e.preventDefault();col.closest(".kanban-column").classList.remove("drag-over");const id=e.dataTransfer.getData("text/plain");if(id)updateTaskField(id,"status",col.dataset.dropStatus);});
 });
 document.addEventListener("dragstart",e=>{const card=e.target.closest("[data-drag-task]");if(card)e.dataTransfer.setData("text/plain",card.dataset.dragTask);});
 $("taskDescription").addEventListener("input",()=>{
  const match=$("taskDescription").value.match(/(?:^|\s)@([a-zA-Z0-9_.-]*)$/);const box=$("mentionSuggestions");
  if(!match){box.classList.add("hidden");return;}
  const q=match[1].toLowerCase(),matches=state.users.filter(u=>u.username.toLowerCase().includes(q)).slice(0,6);
  box.innerHTML=matches.map(u=>`<button type="button" data-insert-mention="${esc(u.username)}">@${esc(u.username)}</button>`).join("");box.classList.toggle("hidden",!matches.length);
 });
 $("mentionSuggestions").addEventListener("click",e=>{const b=e.target.closest("[data-insert-mention]");if(!b)return;const input=$("taskDescription");input.value=input.value.replace(/(?:^|\s)@[a-zA-Z0-9_.-]*$/,` @${b.dataset.insertMention} `);$("mentionSuggestions").classList.add("hidden");input.focus();});
}
function init(){
 $("userName").textContent=currentUsername;$("userAvatar").textContent=(currentUsername[0]||"U").toUpperCase();$("userRole").textContent=isAdmin?"ADMINISTRATOR":"MEMBER";
 if(isAdmin)$("auditNav").classList.remove("hidden");
 $("todayLabel").textContent=new Intl.DateTimeFormat(undefined,{weekday:"long",month:"short",day:"2-digit"}).format(new Date()).toUpperCase();
 setupEvents();loadUsers();loadTasks().then(()=>loadMentions()).catch(()=>{});
}
init();
