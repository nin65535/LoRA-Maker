import { useCallback, useEffect, useState } from "react";

type Job = { id:string; type:string; status:"queued"|"running"|"completed"|"failed"|"cancelled"; payload:Record<string,string>; error:string|null };
type Image = { datasetKey:string; datasetName:string; name:string; relativePath:string };
type Status = { images:Image[]; presets:{key:string;name:string}[] };
async function api<T>(url:string,init?:RequestInit):Promise<T>{const response=await fetch(url,{...init,headers:{"Content-Type":"application/json",...init?.headers}});if(!response.ok){const body=await response.json().catch(()=>({})) as {error?:{message?:string}};throw new Error(body.error?.message??`HTTP ${response.status}`)}return response.json() as Promise<T>}

export function MoviePanel({jobs,onJobs,onScan,onMessage}:{jobs:Job[];onJobs:()=>Promise<void>;onScan:()=>Promise<void>;onMessage:(text:string)=>void}){
 const [status,setStatus]=useState<Status|null>(null),[loading,setLoading]=useState(false);
 const load=useCallback(async()=>{setLoading(true);try{setStatus(await api<Status>("/api/movies"))}catch(e){onMessage(e instanceof Error?e.message:"動画生成状態取得失敗")}finally{setLoading(false)}},[onMessage]);
 useEffect(()=>{void load()},[load,jobs]);
 const queue=async(image:Image,presetKey:string)=>{try{await api("/api/movies/queue",{method:"POST",body:JSON.stringify({datasetKey:image.datasetKey,imagePath:image.relativePath,presetKey})});onMessage("動画生成ジョブを登録しました");await onJobs()}catch(e){onMessage(e instanceof Error?e.message:"動画生成登録失敗")}};
 const retry=async(job:Job)=>{try{await api("/api/movies/queue",{method:"POST",body:JSON.stringify({datasetKey:job.payload.datasetKey,imagePath:job.payload.imagePath,presetKey:job.payload.presetKey})});onMessage("動画生成ジョブを再登録しました");await onJobs()}catch(e){onMessage(e instanceof Error?e.message:"再実行登録失敗")}};
 const movieJobs=jobs.filter(x=>x.type==="movie-generation"), labels:Record<Job["status"],string>={queued:"待機中",running:"生成中",completed:"完了",failed:"失敗",cancelled:"キャンセル"};
 return <section className="panel"><p className="eyebrow">COMFYUI MOVIE GENERATION</p><div className="jobs-head"><div><h2>02_動画</h2><p className="muted">素材画像とプリセットを1組ずつ直列キューへ登録します。</p></div><button disabled={loading} onClick={()=>{void load();void onScan()}}>{loading?"更新中…":"状態を更新"}</button></div>
 {!status?.images.length?<p className="muted">素材画像はありません</p>:<div className="table"><table><thead><tr><th>素材画像</th><th>プリセット</th></tr></thead><tbody>{status.images.map(image=><tr key={`${image.datasetKey}/${image.relativePath}`}><td><strong>{image.name}</strong><small>{image.datasetName} / {image.datasetKey} / {image.relativePath}</small></td><td>{status.presets.map(preset=><button key={preset.key} onClick={()=>void queue(image,preset.key)}>{preset.name}</button>)}</td></tr>)}</tbody></table></div>}
 <h3>動画作成予定キュー・履歴</h3>{!movieJobs.length?<p className="muted">動画生成ジョブはありません</p>:<div className="table"><table><thead><tr><th>状態</th><th>素材 / プリセット</th><th>操作</th></tr></thead><tbody>{movieJobs.map(job=><tr key={job.id}><td><span className={`status status--${job.status}`}><i/>{labels[job.status]}</span>{job.error&&<p className="warn">{job.error}</p>}</td><td>{job.payload.datasetKey} / {job.payload.imagePath}<small>{job.payload.presetKey}</small></td><td>{job.status==="failed"&&<button onClick={()=>void retry(job)}>再実行</button>}</td></tr>)}</tbody></table></div>}</section>
}
