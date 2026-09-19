import { FormEvent, useEffect, useState } from "react";
import "./settings.css";

type Job = { status:string };
export type PersonalSettings = {
  lastProjectConfigPath:string|null;
  comfyuiApiUrl:string;
  comfyuiMovieOutputPath:string|null;
  bandiviewPath:string|null;
  bandiviewSelectionPath:string|null;
  sdScriptsPythonPath:string|null;
  sdScriptsPath:string|null;
  sdScriptsWorkingDirectory:string|null;
  trainingOutputPath:string|null;
  loraModelsPath:string|null;
};

async function api<T>(url:string,init?:RequestInit):Promise<T>{
  const response=await fetch(url,{...init,headers:{"Content-Type":"application/json",...init?.headers}});
  if(!response.ok){const body=await response.json().catch(()=>({})) as {error?:{message?:string}};throw new Error(body.error?.message??`HTTP ${response.status}`)}
  return response.json() as Promise<T>;
}

const fields:{key:Exclude<keyof PersonalSettings,"lastProjectConfigPath">;label:string;group:string}[]=[
  {key:"comfyuiApiUrl",label:"ComfyUI API URL",group:"ComfyUI"},
  {key:"comfyuiMovieOutputPath",label:"動画専用出力フォルダ",group:"ComfyUI"},
  {key:"bandiviewPath",label:"BandiView実行ファイル",group:"BandiView"},
  {key:"bandiviewSelectionPath",label:"選別画像保存フォルダ",group:"BandiView"},
  {key:"sdScriptsPythonPath",label:"Python実行ファイル",group:"kohya / sd-scripts"},
  {key:"sdScriptsPath",label:"学習スクリプトまたはsd-scripts配置先",group:"kohya / sd-scripts"},
  {key:"sdScriptsWorkingDirectory",label:"作業ディレクトリ",group:"kohya / sd-scripts"},
  {key:"trainingOutputPath",label:"学習一時出力フォルダ",group:"kohya / sd-scripts"},
  {key:"loraModelsPath",label:"LoRA modelsフォルダ",group:"kohya / sd-scripts"},
];

export function SettingsPanel({jobs,onMessage,onDirtyChange,onSaved}:{jobs:Job[];onMessage:(text:string)=>void;onDirtyChange:(dirty:boolean)=>void;onSaved:()=>void}){
  const [draft,setDraft]=useState<PersonalSettings|null>(null),[baseline,setBaseline]=useState<PersonalSettings|null>(null),[loading,setLoading]=useState(true),[saving,setSaving]=useState(false);
  const active=jobs.some(job=>job.status==="queued"||job.status==="running");
  const dirty=draft!==null&&baseline!==null&&JSON.stringify(draft)!==JSON.stringify(baseline);
  useEffect(()=>{onDirtyChange(dirty)},[dirty,onDirtyChange]);
  useEffect(()=>{let cancelled=false;setLoading(true);api<PersonalSettings>("/api/projects/settings").then(settings=>{if(!cancelled){setDraft({...settings});setBaseline({...settings})}}).catch(error=>{if(!cancelled)onMessage(error instanceof Error?error.message:"個人設定の読込に失敗しました")}).finally(()=>{if(!cancelled)setLoading(false)});return()=>{cancelled=true;onDirtyChange(false)}},[onDirtyChange,onMessage]);
  const update=(key:Exclude<keyof PersonalSettings,"lastProjectConfigPath">,value:string)=>setDraft(current=>current?{...current,[key]:key==="comfyuiApiUrl"?value:(value||null)}:current);
  const save=async(event:FormEvent)=>{event.preventDefault();if(!draft||!dirty||active)return;setSaving(true);try{const saved=await api<PersonalSettings>("/api/projects/settings",{method:"PUT",body:JSON.stringify(draft)});setDraft({...saved});setBaseline({...saved});onMessage("個人設定を保存しました");onSaved()}catch(error){onMessage(error instanceof Error?error.message:"個人設定の保存に失敗しました")}finally{setSaving(false)}};
  if(loading||!draft)return <section className="panel"><h2>設定</h2><p className="muted">個人設定を読み込み中…</p></section>;
  return <section className="panel"><p className="eyebrow">PERSONAL SETTINGS</p><h2>設定</h2><p className="muted">このPC固有の外部ツールと保存先を集中管理します。</p><form className="settings-form" onSubmit={save}>{["ComfyUI","BandiView","kohya / sd-scripts"].map(group=><fieldset key={group}><legend>{group}</legend>{fields.filter(field=>field.group===group).map(field=><label key={field.key}>{field.label}<input value={draft[field.key]??""} onChange={event=>update(field.key,event.target.value)}/></label>)}</fieldset>)}<div className="settings-actions"><button disabled={saving||active||!dirty}>{active?"処理中は保存できません":saving?"保存中…":"設定を保存"}</button></div></form></section>;
}
