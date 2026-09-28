"use strict";
const home = document.body.dataset.home;
const byID = id => document.getElementById(id);
let settings, inventory, cursor = "";
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function notice(message) { byID("notice").textContent = message; }
async function request(path, options) {
  const response = await fetch(path, {credentials: "same-origin", cache: "no-store", ...options});
  if (!response.ok) throw new Error(response.status === 401 ? "Your session has changed. Reload this page to sign in again." : response.status === 409 ? "This account changed. Refresh accounts before trying again." : "The request could not be completed (" + response.status + ").");
  return response.status === 204 ? null : response.json();
}
function serviceName(service) { return service.name || service.id.replaceAll("-", " "); }
function choiceName(service) { return service.name || service.id; }
function serviceTile(service) {
  const tile = element("a", undefined, "tile"); tile.href = service.url;
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("viewBox", "0 0 24 24"); icon.setAttribute("class", "service-icon"); icon.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(icon.namespaceURI, "path");
  const icons = {
    workbench: "M3 4h18v16H3Z M7 9l3 3-3 3 M13 15h4",
    dashboard: "M3 4h18v16H3Z M7 9l3 3-3 3 M13 15h4",
    observatory: "M3 3v18h18 M7 16v-5 M12 16V6 M17 16v-8",
    grafana: "M3 3v18h18 M7 16v-5 M12 16V6 M17 16v-8",
    pi: "M4 7h16 M9 7v13 M16 7v10q0 3 4 3",
    "pi-web": "M4 7h16 M9 7v13 M16 7v10q0 3 4 3",
    "hindsight-ui": "M2.5 9.5S6 4 12 4s9.5 5.5 9.5 5.5S18 15 12 15 2.5 9.5 2.5 9.5Z M12 7a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5 M19 15a7.5 7.5 0 0 1-7 4H6 M9 16l-3 3 3 3",
  };
  path.setAttribute("d", icons[service.id] || "M3 3h7v7H3Z M14 3h7v7h-7Z M3 14h7v7H3Z M14 14h7v7h-7Z");
  icon.append(path);
  tile.append(icon, element("h3", serviceName(service)));
  if (service.managed_role && service.role) tile.append(element("span", service.role, "role"));
  return tile;
}
function accountEditor(user) {
  const form = element("form", undefined, "user");
  form.append(element("h3", (user.username || "Username unavailable") + (user.administrator ? " · Connect operator" : ""), "user-title"));
  form.append(element("code", user.id, "user-id"));
  const fields = element("fieldset"); fields.append(element("legend", "Service entitlements"));
  const choices = new Map();
  for (const service of settings.choices) {
    const label = element("label", choiceName(service), "grant"), select = element("select");
    const current = (user.resources || []).includes(service.id) ? (user.application_roles?.[service.id] || "legacy") : "none";
    const roleOptions = service.managed_role ? [["member","Member"],["admin","Admin"]] : [[current === "admin" ? "admin" : "member","Access · app sets role"]];
    for (const [value,text] of [["none","No access"],...roleOptions, ...(current === "legacy" ? [["legacy","App-managed role"]] : [])]) {
      const option = element("option", text); option.value = value; select.append(option);
    }
    select.value = current; label.append(select); fields.append(label); choices.set(service.id,select);
  }
  const controls = element("div", undefined, "user-controls"), disabledLabel = element("label", " Disable account "), disabled = element("input");
  disabled.type = "checkbox"; disabled.checked = user.disabled; disabledLabel.prepend(disabled);
  const save = element("button", "Save access"); save.type = "submit";
  controls.append(disabledLabel, save); form.append(fields, controls);
  if (user.deleting) {
    fields.disabled = true; disabled.disabled = true; save.disabled = true;
    form.append(element("p", "Deletion in progress. Sign-in is disabled. Refresh accounts to check completion."));
  } else if (settings.user_deletion) {
    const remove = element("button", "Delete user"); remove.type = "button";
    remove.disabled = user.administrator || user.id === inventory.current_principal;
    if (remove.disabled) remove.title = "Connect operators must be removed from the operator configuration before deletion.";
    remove.addEventListener("click", async () => {
      const label = user.username || user.id;
      if (!window.confirm("Permanently delete " + label + "? This removes the account, identity and Connect sessions. The username can be reused. Application data and retained backups are not deleted.")) return;
      remove.disabled = true;
      try {
        await request(settings.administration_path, {method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":inventory.csrf},body:JSON.stringify({action:"human-delete",request_id:crypto.randomUUID(),expected_generation:user.generation,csrf:inventory.csrf,principal:user.id})});
        notice("Deletion requested. Sign-in is disabled while the account is removed."); await loadUsers("");
      } catch (error) { notice(error.message); remove.disabled = false; }
    });
    controls.append(remove);
  }
  form.addEventListener("submit", async event => {
    event.preventDefault(); save.disabled = true;
    // Preserve grants unknown to this browser's declaration. A displayed form
    // must not silently remove another independently configured resource.
    const resources = (user.resources || []).filter(id => !choices.has(id));
    const roles = Object.fromEntries(Object.entries(user.application_roles || {}).filter(([id]) => !choices.has(id)));
    for (const [id, select] of choices) {
      if (select.value !== "none") resources.push(id);
      if (["member","admin"].includes(select.value)) roles[id] = select.value;
    }
    if (!resources.length) { notice("Keep at least one service and disable the account to suspend all access."); save.disabled = false; return; }
    try {
      await request(settings.administration_path, {method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":inventory.csrf},body:JSON.stringify({action:"human-update",request_id:crypto.randomUUID(),expected_generation:user.generation,csrf:inventory.csrf,principal:user.id,disabled:disabled.checked,resources,application_roles:roles})});
      notice("Access saved. Previous sessions for this account are invalidated."); await loadUsers("");
    } catch (error) { notice(error.message); } finally { save.disabled = false; }
  });
  return form;
}
async function loadUsers(next) {
  inventory = await request(settings.administration_path + "?" + new URLSearchParams({kind:"users",limit:"20",...(next ? {cursor:next} : {})}));
  // ponytail: Sorting is page-local because this request is capped at 20
  // accounts; preserve the API cursor's backend key order across pages.
  const accounts = [...inventory.items].sort((left, right) =>
    Number(left.disabled) - Number(right.disabled) ||
    (left.username || left.id).localeCompare(right.username || right.id) ||
    left.id.localeCompare(right.id));
  byID("users").replaceChildren(...accounts.map(accountEditor));
  cursor = inventory.next_cursor; byID("next-users").hidden = !cursor;
}
byID("terminal").addEventListener("click", () => { const panel=byID("terminal-help"); panel.hidden=!panel.hidden; byID("terminal").setAttribute("aria-expanded", String(!panel.hidden)); });
byID("refresh-users").addEventListener("click", () => loadUsers("").catch(error => notice(error.message)));
byID("next-users").addEventListener("click", () => loadUsers(cursor).catch(error => notice(error.message)));
byID("logout").addEventListener("click", async () => {
  try {
    const response = await fetch(settings.logout_path, {method:"POST",credentials:"same-origin",redirect:"manual"});
    if (!response.ok && response.type !== "opaqueredirect") throw new Error("Sign-out failed. Try again.");
    clearKeySecret(); byID("key-confirm").close("cancel"); byID("router-keys").hidden=true; byID("key-list").replaceChildren(); byID("router-accounts").replaceChildren();
    byID("services").replaceChildren(); byID("service-count").textContent="0 enabled"; byID("administration").hidden=true; byID("manage-access-link").hidden=true; byID("logout").disabled=true; notice("Signed out of Connect. Your account provider may still have an active sign-in.");
  } catch(error) { notice(error.message); }
});
(async () => {
  try {
    settings = await request(home+"/data");
    byID("services").replaceChildren(...settings.services.map(serviceTile));
    byID("service-count").textContent = settings.services.length + " enabled";
    const account=byID("account-link"); account.href=settings.account_url; account.hidden=false;
    const passkeys=byID("passkeys-link"); passkeys.href=settings.passkeys_url; passkeys.hidden=false;
    if(settings.administration_url) { const link=byID("manage-access-link"); link.href=settings.administration_url; link.hidden=false; }
    notice(settings.services.length ? "" : "No services have been assigned. Contact your operator.");
    if(settings.router_keys) { byID("router-keys").hidden=false; await loadRouterKeys().catch(error=>keyNotice(error.message,true)); }
    if(settings.administration_path) { byID("administration").hidden=false; await loadUsers(""); }
  } catch(error) { notice(error.message); }
})();

let keyState, keyCSRF = "";
function keyNotice(message, error=false) {
  const node=byID("key-notice"); node.dataset.error=String(error); node.setAttribute("role",error?"alert":"status"); node.textContent=message; if(error) node.scrollIntoView({block:"nearest"});
}
async function keyRequest(operation) {
  const response=await fetch(home+"/router-keys",{credentials:"same-origin",cache:"no-store",
    ...(operation?{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":keyCSRF},body:JSON.stringify(operation)}:{})});
  if(response.status===401) clearKeySecret();
  if(!response.ok) throw new Error(response.status===401?"Your session changed. Reload this page to sign in again.":response.status===403?"Your access or limits changed. Refresh before trying again.":response.status===429?"Too many requests. Wait a moment, then refresh.":response.status===400?"Check the name, permissions, and limits, then try again.":"Router access is temporarily unavailable. Wait a moment, then refresh.");
  keyCSRF=response.headers.get("X-CSRF-Token")||keyCSRF; return response.json();
}
function clearKeySecret() {
  byID("copy-key").textContent="Copy API key"; byID("key-secret").value=""; byID("key-secret").type="password"; byID("new-key").hidden=true;
  byID("reveal-key").textContent="Show key"; byID("reveal-key").setAttribute("aria-pressed","false");
}
function confirmKeyAction(title,description,label) {
  const dialog=byID("key-confirm"); byID("key-confirm-title").textContent=title; byID("key-confirm-description").textContent=description; byID("key-confirm-action").textContent=label;
  dialog.returnValue="cancel";
  return new Promise(resolve=>{dialog.addEventListener("close",()=>resolve(dialog.returnValue==="confirm"),{once:true});dialog.showModal();});
}
const endpointNames={"/v1/chat/completions":"Chat · OpenAI","/v1/messages":"Messages · Anthropic","/v1/responses":"Responses · OpenAI","/v1/embeddings":"Embeddings","/v1/rerank":"Reranking"};
function keyChoices(container,values,selected) {
  const legend=container.querySelector("legend"); container.replaceChildren(legend);
  for(const value of values) {
    const label=element("label",endpointNames[value]?endpointNames[value]+" ("+value+")":value),input=element("input");
    input.type="checkbox"; input.value=value; input.checked=selected.includes(value); label.prepend(input); container.append(label);
  }
}
function selectedKeys(container) { return [...container.querySelectorAll("input:checked")].map(input=>input.value); }
function formatKeyTime(seconds) { return seconds?new Date(seconds*1000).toLocaleString():"Never"; }
function activeKey(key) { return keyState?.account?.status==="approved"&&!key.revoked_at&&(!key.expires_at||key.expires_at>Date.now()/1000); }
function keyBadge(text,state) { const badge=element("span",text,"key-badge");badge.dataset.state=state;return badge; }
function showCreateKey(show) {
  byID("create-key-panel").hidden=!show; byID("open-create-key").setAttribute("aria-expanded",String(show));
  if(!show) return;
  const account=keyState.account,form=byID("create-key");
  form.reset(); keyChoices(byID("key-models"),account.models,account.models);
  keyChoices(byID("key-paths"),account.paths.filter(path=>path!=="/v1/models"),account.paths);
  form.elements.rpm.max=account.rpm;form.elements.rpm.value=account.rpm;
  form.elements.expires_days.max=account.expires_days;form.elements.expires_days.value=Math.min(30,account.expires_days);
  byID("key-budget").textContent="All your keys share "+account.rpm.toLocaleString()+" requests per minute. A new key does not increase that allowance.";
  form.elements.name.focus();
}
function keyRow(key,usage) {
  const row=element("article",undefined,"key-row"),heading=element("div",undefined,"key-row-heading"),identity=element("div");
  const expired=key.expires_at&&key.expires_at<=Date.now()/1000;
  const status=key.revoked_at?"Revoked":expired?"Expired":activeKey(key)?"Active":"Access inactive";
  identity.append(element("h3",key.name),keyBadge(status,status.toLowerCase()));heading.append(identity);
  if(!key.revoked_at&&!expired) {
    const revoke=element("button","Revoke key");revoke.type="button";revoke.setAttribute("aria-label","Revoke key for "+key.name);
    revoke.addEventListener("click",async()=>{
      if(!await confirmKeyAction("Revoke “"+key.name+"”?","Apps using this key will lose access. Requests already running may finish. Create a replacement first if you need uninterrupted access.","Revoke key")) return;
      revoke.disabled=true;
      try { await keyRequest({action:"revoke",key_id:key.key_id});await loadRouterKeys();keyNotice("“"+key.name+"” was revoked.");byID("refresh-keys").focus(); }
      catch(error) {keyNotice(error.message,true);revoke.disabled=false;}
    });heading.append(revoke);
  }
  row.append(heading,element("p",key.models.join(", ")+" · Expires "+formatKeyTime(key.expires_at)),
    element("p",usage?usage.requests.toLocaleString()+" requests · Last used "+formatKeyTime(usage.last_used):"Not used in the available history."));
  const details=element("details",undefined,"key-detail");details.append(element("summary","Permissions and key details"),element("code",key.key_id,"user-id"),
    element("p","Expires "+formatKeyTime(key.expires_at)+" · "+key.rpm.toLocaleString()+" requests per minute"),element("p",key.paths.join(", ")));
  if(usage) details.append(element("p",usage.errors+" errors · "+usage.rate_limited+" rate limited · Average recorded duration "+Math.round(usage.average_ms)+" ms"));
  row.append(details);return row;
}
function routerAccountEditor(account) {
  const row=element("details",undefined,"router-account"),heading=element("summary"),available=account.available===true;
  const label=account.username||"Account "+account.owner.slice(6,14);
  heading.append(element("span",label),keyBadge(available?account.status:"Account changed",available?account.status:"inactive"));row.append(heading);
  row.open=available&&account.status==="pending";
  row.append(element("code",account.owner,"user-id"));
  if(available) {
    const form=element("form"),models=element("fieldset"),paths=element("fieldset");
    models.append(element("legend","Allowed models"));paths.append(element("legend","Allowed API endpoints"));
    keyChoices(models,keyState.models,account.models);keyChoices(paths,keyState.paths,account.paths);
    const rpm=element("input"),days=element("input"),rpmLabel=element("label","Shared requests per minute"),daysLabel=element("label","Maximum key lifetime (days)");
    rpm.type=days.type="number";rpm.min=days.min="1";rpm.max="100000";days.max="90";rpm.required=days.required=true;
    rpm.value=account.rpm||60;days.value=account.expires_days||30;rpmLabel.append(rpm);daysLabel.append(days);
    const approved=account.status==="approved",save=element("button",approved?"Save limits and revoke keys":"Approve access","primary"),deny=element("button",approved?"Remove access":"Decline request","danger"),actions=element("div",undefined,"key-actions");
    save.type="submit";deny.type="button";deny.hidden=account.status==="denied";actions.append(save,deny);
    if(approved) {
      save.disabled=true;
      form.addEventListener("input",()=>{save.disabled=Number(rpm.value)===account.rpm&&Number(days.value)===account.expires_days&&JSON.stringify(selectedKeys(models).sort())===JSON.stringify([...account.models].sort())&&JSON.stringify(selectedKeys(paths).sort())===JSON.stringify(account.paths.filter(path=>path!=="/v1/models").sort());});
    }
    form.append(models,paths,rpmLabel,daysLabel,element("p",approved?"Saving these limits revokes all of this person’s existing keys. They will need to create replacements.":"After approval, this person can create up to 10 keys within these limits. All keys share the account’s request allowance.",approved?"account-warning":"account-help"),actions);
    const update=async status=>{
      const selectedModels=selectedKeys(models),selectedPaths=selectedKeys(paths);
      if(status==="approved"&&(!selectedModels.length||selectedModels.length>64||!selectedPaths.length)) {keyNotice("Choose 1–64 models and at least one API endpoint.",true);return;}
      if(approved&&!await confirmKeyAction(status==="denied"?"Remove access for "+label+"?":"Change limits for "+label+"?","All existing router keys for this account will be revoked. Apps using them will lose access.",status==="denied"?"Remove access":"Save limits and revoke keys")) return;
      if(!approved&&status==="denied"&&!await confirmKeyAction("Decline this request?",label+" will not be able to create router keys. You can approve access later.","Decline request")) return;
      save.disabled=deny.disabled=true;
      try {await keyRequest({action:"approve",owner:account.owner,revision:account.revision,status,models:selectedModels,paths:selectedPaths,rpm:Number(rpm.value),expires_days:Number(days.value)});await loadRouterKeys();keyNotice(status==="approved"?"Router access approved for "+label+".":"Router access removed for "+label+".");byID("refresh-keys").focus();}
      catch(error) {keyNotice(error.message,true);save.disabled=deny.disabled=false;}
    };
    form.addEventListener("submit",event=>{event.preventDefault();update("approved");});deny.addEventListener("click",()=>update("denied"));row.append(form);
  } else row.append(element("p","This account changed or was deleted. The person must request access again before you can approve it."));
  if(!available||account.status==="denied") {
    const cleanup=element("details",undefined,"record-actions"),forget=element("button","Remove inactive record");forget.type="button";
    cleanup.append(element("summary","Record options"),element("p","Remove this inactive request from the list. Retained usage stays available."),forget);
    forget.addEventListener("click",async()=>{
      if(!await confirmKeyAction("Remove this inactive record?","This removes the request for "+label+" from this list. It does not delete the person’s Connect account or retained usage.","Remove record")) return;
      forget.disabled=true;
      try {await keyRequest({action:"forget",owner:account.owner,revision:account.revision});await loadRouterKeys();keyNotice("Inactive request removed.");}
      catch(error) {keyNotice(error.message,true);forget.disabled=false;}
    });row.append(cleanup);
  }
  return row;
}
async function loadRouterKeys() {
  const previous=keyState; keyState=await keyRequest();const account=keyState.account,approved=account?.status==="approved";
  const states={approved:["Access approved","Your keys share "+(account?.rpm||0).toLocaleString()+" requests per minute. Each key can have narrower permissions."],pending:["Awaiting approval","Your request was sent. An operator will choose your models and limits. Refresh here to check for an update."],denied:["Access not approved","Contact your operator if you still need router access. They can review this request again."],none:["Approval needed","Request access from your operator. Once approved, you can create a separate key for each device."]};
  const state=account?.status||"none";byID("key-status").textContent=states[state][0];byID("key-status").dataset.state=state;byID("key-access").textContent=states[state][1];
  byID("request-api-access").hidden=!!account;byID("key-workspace").hidden=!approved&&!keyState.keys.length&&!keyState.usage_totals.requests;
  const active=keyState.keys.filter(activeKey).length,create=byID("open-create-key");create.hidden=!approved;create.disabled=active>=10||!byID("new-key").hidden;
  byID("key-capacity").hidden=!approved||active<10;byID("key-count").textContent=active+" active";
  if(!approved||active>=10||previous?.account?.revision!==account?.revision) showCreateKey(false);
  if(previous?.account&&previous.account.revision!==account?.revision) clearKeySecret();
  const totals=keyState.usage_totals;
  byID("key-summary").replaceChildren(...[["Requests",totals.requests],["Errors",totals.errors],["Rate limited",totals.rate_limited]].map(([label,value])=>{const card=element("div",undefined,"key-metric");card.append(element("strong",value.toLocaleString()),element("span",label));return card;}));
  byID("key-usage-window").textContent=keyState.usage_window+" Counts can be incomplete when logging is busy.";
  byID("key-list").replaceChildren(...(keyState.keys.length?keyState.keys.map(key=>keyRow(key,keyState.usage.find(item=>item.key_id===key.key_id))):[element("p",approved?"No keys yet. Create your first API key to connect an app or device.":"No keys to show.","key-empty")]));
  byID("key-list-note").textContent=keyState.keys_truncated?"Showing 100 keys, with active keys first. Usage totals include older keys.":"";
  byID("router-approvals").hidden=keyState.accounts===null;
  byID("review-router-access").hidden=keyState.accounts===null;
  const accounts=[...(keyState.accounts||[])].sort((a,b)=>Number(b.available&&b.status==="pending")-Number(a.available&&a.status==="pending")||a.updated_at-b.updated_at);
  byID("approval-count").textContent=accounts.filter(a=>a.available&&a.status==="pending").length+" pending";
  byID("router-accounts").replaceChildren(...(accounts.length?accounts.map(routerAccountEditor):[element("p","No access requests yet. New requests will appear here.","key-empty")]));
}
byID("refresh-keys").addEventListener("click",async()=>{const button=byID("refresh-keys");button.disabled=true;try {await loadRouterKeys();keyNotice("Keys and access are up to date.");}catch(error){keyNotice(error.message,true);}finally{button.disabled=false;}});
byID("open-create-key").addEventListener("click",()=>showCreateKey(true));
byID("cancel-create-key").addEventListener("click",()=>{showCreateKey(false);byID("open-create-key").focus();});
byID("request-api-access").addEventListener("click",async()=>{
  const button=byID("request-api-access");button.disabled=true;
  try {await keyRequest({action:"request"});await loadRouterKeys();keyNotice("Request sent. Your operator can now review it.");byID("refresh-keys").focus();}
  catch(error){keyNotice(error.message,true);}finally{button.disabled=false;}
});
byID("create-key").addEventListener("submit",async event=>{
  event.preventDefault();const form=event.currentTarget,button=form.querySelector("button[type=submit]"),models=selectedKeys(byID("key-models")),paths=selectedKeys(byID("key-paths"));
  if(!models.length||!paths.length){byID("key-advanced")?.setAttribute("open","");keyNotice("Choose at least one model and API endpoint.",true);return;}
  button.disabled=true;
  try {
    const result=await keyRequest({action:"create",name:form.elements.name.value,models,paths,rpm:Number(form.elements.rpm.value),expires_days:Number(form.elements.expires_days.value),revision:keyState.account.revision});
    clearKeySecret();byID("key-secret").value=result.secret;byID("new-key").hidden=false;showCreateKey(false);byID("open-create-key").disabled=true;byID("new-key-heading").focus();keyNotice("Key created. Save it before leaving this page.");
    try {await loadRouterKeys();}catch{keyNotice("Your key was created. Save it now; the key list could not be refreshed.",true);}
  }catch(error){keyNotice(error.message+" If creation was interrupted, refresh and revoke any unused key before retrying.",true);}
  finally{button.disabled=false;}
});
byID("copy-key").addEventListener("click",async()=>{try {await navigator.clipboard.writeText(byID("key-secret").value);byID("copy-key").textContent="Copied";keyNotice("Key copied. Save it in your app’s credential settings.");}catch{byID("key-secret").type="text";byID("reveal-key").textContent="Hide key";byID("reveal-key").setAttribute("aria-pressed","true");byID("key-secret").focus();byID("key-secret").select();keyNotice("Clipboard access is unavailable. Copy the selected key manually.",true);}});
byID("reveal-key").addEventListener("click",()=>{const shown=byID("key-secret").type==="password";byID("key-secret").type=shown?"text":"password";byID("reveal-key").textContent=shown?"Hide key":"Show key";byID("reveal-key").setAttribute("aria-pressed",String(shown));});
byID("dismiss-key").addEventListener("click",()=>{clearKeySecret();byID("copy-key").textContent="Copy API key";byID("open-create-key").disabled=keyState.keys.filter(activeKey).length>=10;byID("refresh-keys").focus();keyNotice("Key saved. You can review its usage below.");});
window.addEventListener("pagehide",clearKeySecret);
