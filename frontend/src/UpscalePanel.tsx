import { useCallback, useEffect, useState } from "react";
import "./upscale.css";
import "./process-table.css";

type Job = { type:string; status:string };
type Folder = { datasetKey:string; datasetName:string; captureFolder:string; captureCount:number; selected:boolean; selectedImageCount:number; scale1ProcessedCount:number; scale2ProcessedCount:number; state:"idle"|"selected"|"queued"|"running"|"failed"; error:string|null };
type Status = { activeTarget:{datasetKey:string;captureFolder:string}|null; bandiviewRunning:boolean; folders:Folder[] };

async function api<T>(url:string,init?:RequestInit):Promise<T>{
 const response=await fetch(url,{...init,headers:{"Content-Type":"application/json",...init?.headers}});
 if(!response.ok){const body=await response.json().catch(()=>({})) as {error?:{message?:string}};throw new Error(body.error?.message??`HTTP ${response.status}`)}
 return response.json() as Promise<T>;
}

export function UpscalePanel({jobs,bandiviewRevision,onJobs,onScan,onMessage}:{jobs:Job[];bandiviewRevision:number;onJobs:()=>Promise<void>;onScan:()=>Promise<void>;onMessage:(text:string)=>void}){
 const [status,setStatus]=useState<Status|null>(null),[loading,setLoading]=useState(false);
 const load=useCallback(async()=>{setLoading(true);try{setStatus(await api<Status>("/api/upscale"))}catch(e){onMessage(e instanceof Error?e.message:"拡大状態取得失敗")}finally{setLoading(false)}},[onMessage]);
 useEffect(()=>{void load()},[load,jobs,bandiviewRevision]);
 const select=async(row:Folder)=>{try{await api(`/api/upscale/${row.datasetKey}/${encodeURIComponent(row.captureFolder)}/select`,{method:"POST"});onMessage("BandiViewを起動しました。終了後に選別画像数を自動更新します");await load()}catch(e){onMessage(e instanceof Error?e.message:"BandiView起動失敗")}};
 const upscale=async(row:Folder,scale:1|2)=>{try{await api(`/api/upscale/${row.datasetKey}/${encodeURIComponent(row.captureFolder)}/run`,{method:"POST",body:JSON.stringify({scale})});onMessage(`拡大×${scale}と後続の自動タグ付けを登録しました`);await onJobs();await load()}catch(e){onMessage(e instanceof Error?e.message:"拡大登録失敗")}};
 const openInput=async(row:Folder)=>{try{await api(`/api/upscale/${encodeURIComponent(row.datasetKey)}/${encodeURIComponent(row.captureFolder)}/open-input`,{method:"POST"})}catch(e){onMessage(e instanceof Error?e.message:"キャプチャフォルダを開けません")}};
 const labels:Record<Folder["state"],string>={idle:"未選別",selected:"選別済み",queued:"待機中",running:"拡大中",failed:"失敗"};
 return <section className="panel"><p className="eyebrow">SELECTION & UPSCALE</p><div className="jobs-head"><div><h2>04_選別・拡大</h2><p className="muted">選別原寸画像を保管し、×2または×1の画像を学習素材へ追加します。完了後は自動タグ付けします。</p></div><button disabled={loading} onClick={()=>{void load();void onScan()}}>{loading?"更新中…":"状態を更新"}</button></div>
 {!status?.folders.length?<p className="muted">画像の入ったキャプチャフォルダはありません</p>:<div className="table process-table"><table><thead><tr><th>データセット</th><th>加工前のデータ（キャプチャ画像）</th><th>加工操作（選別・拡大）</th><th>状態</th><th>×2 配置数</th><th>×1 配置数</th></tr></thead><tbody>{status.folders.map(row=>{const noSelection=row.selectedImageCount===0;return <tr key={`${row.datasetKey}/${row.captureFolder}`}><td><strong>{row.datasetName}</strong><small>{row.datasetKey}</small></td><td><div className="process-input"><button onClick={()=>void openInput(row)}>フォルダを開く</button><div><strong>{row.captureFolder}</strong><small>{row.captureCount} 枚</small></div></div></td><td className="process-actions"><button disabled={status.bandiviewRunning||row.captureCount===0} onClick={()=>void select(row)}>選別</button><button disabled={noSelection} onClick={()=>void upscale(row,2)}>拡大×2</button><button disabled={noSelection} onClick={()=>void upscale(row,1)}>拡大×1</button></td><td><span className={`status status--${row.state}`}><i/>{status.bandiviewRunning&&row.selected?"BandiView実行中":labels[row.state]}</span><small>選別済み: {row.selectedImageCount} 枚</small>{row.error&&<p className="warn">{row.error}</p>}</td><td>{row.scale2ProcessedCount} 枚</td><td>{row.scale1ProcessedCount} 枚</td></tr>})}</tbody></table></div>}
 </section>;
}
