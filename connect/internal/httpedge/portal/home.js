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
    if(settings.administration_path) { byID("administration").hidden=false; await loadUsers(""); }
  } catch(error) { notice(error.message); }
})();
