import { useCallback, useEffect, useState } from "react";
import "./training.css";

type Job = { id:string; type:string; status:string; payload:Record<string,string>; logs:string[] };
type Dataset = { key:string; name:string; imageCount:number; captionCount:number; matchedPairs:number; imagesWithoutCaptions:string[]; captionsWithoutImages:string[] };
type Artifact = { name:string; size:number; deployment:"unconfigured"|"not_deployed"|"identical"|"different" };
type Status = { datasets:Dataset[]; artifacts:Artifact[]; configured:boolean; configurationErrors:string[]; trainingConfigs:string[]; nextOutputName:string };

async function api<T>(url:string,init?:RequestInit):Promise<T>{const response=await fetch(url,{...init,headers:{"Content-Type":"application/json",...init?.headers}});if(!response.ok){const body=await response.json().catch(()=>({})) as {error?:{message?:string}};throw new Error(body.error?.message??`HTTP ${response.status}`)}return response.json() as Promise<T>}

function trainingProgress(job:Job|null){
 if(!job)return null;
 let current=0,total=0;
 for(const line of job.logs){
  for(const match of line.matchAll(/(\d+)\s*\/\s*(\d+)/g)){
   const nextCurrent=Number(match[1]),nextTotal=Number(match[2]);
   if(nextTotal>0&&nextCurrent<=nextTotal&&(nextTotal>total||nextCurrent>=current)){current=nextCurrent;total=nextTotal}
  }
 }
 if(job.status==="completed"&&!total)return {current:1,total:1,percent:100};
 return total?{current,total,percent:Math.min(100,Math.floor(current/total*100))}:null;
}

export function TrainingPanel({jobs,onJobs,onScan,onMessage}:{jobs:Job[];onJobs:()=>Promise<void>;onScan:()=>Promise<void>;onMessage:(s:string)=>void}){
 const [status,setStatus]=useState<Status|null>(null),[configName,setConfigName]=useState(""),[loading,setLoading]=useState(false);
 const active=jobs.some(job=>job.type==="lora-training"&&(job.status==="queued"||job.status==="running"));
 const trainingJob=jobs.find(job=>job.type==="lora-training"&&(job.status==="queued"||job.status==="running"))??jobs.find(job=>job.type==="lora-training")??null;
 const progress=trainingProgress(trainingJob);
 const load=useCallback(async()=>{setLoading(true);try{const nextStatus=await api<Status>("/api/training");setStatus(nextStatus);setConfigName(current=>nextStatus.trainingConfigs.includes(current)?current:(nextStatus.trainingConfigs[0]??""))}catch(error){onMessage(error instanceof Error?error.message:"学習状態取得失敗")}finally{setLoading(false)}},[onMessage]);
 useEffect(()=>{void load()},[load,jobs]);
 const run=async()=>{try{await api("/api/training/run",{method:"POST",body:JSON.stringify({configName})});await onJobs();await load()}catch(error){onMessage(error instanceof Error?error.message:"学習登録失敗")}};
 const action=async(artifact:Artifact,kind:"deploy"|"remove")=>{const mismatch=artifact.deployment==="different";if(mismatch&&!window.confirm(`${artifact.name} は原本と内容が異なります。操作を続けますか？`))return;try{setStatus(await api(`/api/training/artifacts/${encodeURIComponent(artifact.name)}/${kind}`,{method:"POST",body:JSON.stringify({confirmMismatch:mismatch})}));await onScan()}catch(error){onMessage(error instanceof Error?error.message:"成果物操作失敗")}};
 const labels={unconfigured:"配置先未設定",not_deployed:"未配置",identical:"配置済み・同一",different:"配置済み・内容不一致"};
 return <section className="panel"><p className="eyebrow">LORA TRAINING</p><div className="jobs-head"><div><h2>06_LoRA学習素材</h2><p className="muted">sd-scriptsで学習し、成果物を07_LoRAへ保存します。</p></div><button disabled={loading} onClick={()=>void load()}>{loading?"更新中…":"状態を再読込"}</button></div>
 {status?.configurationErrors.map(error=><p className="warn" key={error}>{error}</p>)}<div className="table"><table><thead><tr><th>データセット</th><th>画像</th><th>キャプション</th><th>正常ペア</th><th>不一致</th></tr></thead><tbody>{status?.datasets.map(dataset=><tr key={dataset.key}><td><strong>{dataset.name}</strong><small>{dataset.key}</small></td><td>{dataset.imageCount}</td><td>{dataset.captionCount}</td><td>{dataset.matchedPairs}</td><td>{[...dataset.imagesWithoutCaptions,...dataset.captionsWithoutImages].join(", ")||"なし"}</td></tr>)}</tbody></table></div>
 <div className="training-run"><label>学習設定<select value={configName} onChange={event=>setConfigName(event.target.value)}>{status?.trainingConfigs.map(config=><option key={config} value={config}>{config}</option>)}</select></label><p className="training-output-name">成果物名 <code>{status?.nextOutputName??"—"}</code></p><button disabled={active||!status?.configured||!configName} onClick={()=>void run()}>{active?"学習中…":"学習開始"}</button></div>
 {trainingJob&&<div className="training-progress" aria-live="polite"><div className="training-progress-head"><strong>{trainingJob.payload.outputName??"LoRA学習"}</strong><span>{trainingJob.status==="queued"?"待機中":progress?`${progress.percent}%（${progress.current.toLocaleString()} / ${progress.total.toLocaleString()} steps）`:trainingJob.status==="running"?"準備中…":trainingJob.status==="completed"?"完了":trainingJob.status==="failed"?"失敗":"キャンセル"}</span></div><progress max={progress?.total??1} value={progress?.current??0}/></div>}
 <h2>07_LoRA</h2><div className="table"><table><thead><tr><th>成果物</th><th>サイズ</th><th>models配置</th><th>操作</th></tr></thead><tbody>{status?.artifacts.map(artifact=><tr key={artifact.name}><td><strong>{artifact.name}</strong></td><td>{artifact.size.toLocaleString()} bytes</td><td>{labels[artifact.deployment]}</td><td><button disabled={artifact.deployment==="identical"} onClick={()=>void action(artifact,"deploy")}>{artifact.deployment==="different"?"確認して上書き":"配置"}</button> <button disabled={artifact.deployment==="not_deployed"||artifact.deployment==="unconfigured"} onClick={()=>void action(artifact,"remove")}>配置先から削除</button></td></tr>)}</tbody></table></div>{!status?.artifacts.length&&<p className="muted">学習成果物はありません。</p>}</section>
}
