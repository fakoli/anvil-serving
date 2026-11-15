import { existsSync, readFileSync } from "node:fs";
export const state = {alive:true, cwd:"", calls:[] as any[], commands:[{name:"review",source:"prompt",description:"Review"}], pending:[] as any[], path:null as string|null, historyRequests:[] as Request[], starts:[] as any[], opens:[] as string[], gate:undefined as Promise<void>|undefined};
const owner = {
  isAlive:()=>state.alive, isRunning:()=>false, get cwd(){return state.cwd},
  // This is the actual pinned RpcSessionWrapper contract: onEvent synchronously
  // replays pendingUiRequests before returning its unsubscribe callback.
  onEvent(callback:(event:any)=>void) { for(const event of state.pending) callback(event); return ()=>{}; },
  async send(command:any) {
    state.calls.push(command);
    if(command.type==="get_commands") return {commands:state.commands};
    if(command.type==="get_state") return {isStreaming:false};
    if(command.type==="extension_ui_response") state.pending=state.pending.filter(event=>event.id!==command.id);
    if(command.type==="prompt" && state.gate) await state.gate;
    return {};
  }
};
export const getRpcSession=()=>state.alive?owner:undefined;
export const getRpcSessionInfos=()=>[];
export const startRpcSession=async(id:string,path:string,cwd:string,options:any={})=>{
  state.starts.push({id,path,cwd,options});state.alive=true;
  state.cwd=path?SessionManager.open(path).getCwd():cwd;return {session:owner};
};
export const listAllSessions=async()=>[];
export const mergeSessionLists=(a:any)=>a;
export const resolveSessionPath=async()=>state.path;
// Pinned SDK falls back to process.cwd() when a cached deferred file is absent.
export const SessionManager={open:(path:string)=>{state.opens.push(path);return {getCwd:()=>existsSync(path)?JSON.parse(readFileSync(path,"utf8")).cwd:"/fixture/default-cwd"}}};
export const GET=async(req:Request)=>{state.historyRequests.push(req);return Response.json({context:{messages:[],hasMore:false,entryIds:[]}})};
