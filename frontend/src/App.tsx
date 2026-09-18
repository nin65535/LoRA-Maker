import { FormEvent, useCallback, useEffect, useState } from "react";

type ConnectionState = "checking" | "connected" | "failed";
type Dataset = { key: string; name: string; repeats: number; triggerTags: string[]; removedTags: string[] };
type ProjectState = { configPath: string; rootPath: string; config: { application: "lora-maker"; schemaVersion: 1; project: { name: string }; datasets: Dataset[] }; warnings: string[] };
const labels = { checking: "確認中", connected: "接続済み", failed: "接続できません" };
const splitTags = (value: string) => value.split(",").map((tag) => tag.trim()).filter(Boolean);

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { error?: { message?: string } };
    throw new Error(body.error?.message ?? `HTTP ${response.status}`);
  }
  return await response.json() as T;
}

export function App() {
  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [project, setProject] = useState<ProjectState | null>(null);
  const [message, setMessage] = useState("");
  const [newRoot, setNewRoot] = useState("");
  const [newName, setNewName] = useState("");
  const [dataset, setDataset] = useState({ key: "", name: "", repeats: 10, tags: "" });
  const [busy, setBusy] = useState(false);

  const checkHealth = useCallback(async () => {
    setConnection("checking");
    try { await api("/api/health"); setConnection("connected"); } catch { setConnection("failed"); }
  }, []);
  const loadCurrent = useCallback(async () => {
    try { setProject(await api<ProjectState | null>("/api/projects/current")); }
    catch (error) { setMessage(error instanceof Error ? error.message : "読込に失敗しました"); }
  }, []);
  useEffect(() => { void checkHealth(); void loadCurrent(); }, [checkHealth, loadCurrent]);

  async function perform(action: () => Promise<ProjectState>, success: string) {
    setBusy(true); setMessage("");
    try { const result = await action(); setProject(result); setMessage(success); }
    catch (error) { setMessage(error instanceof Error ? error.message : "操作に失敗しました"); }
    finally { setBusy(false); }
  }
  const selectProject = async () => {
    setBusy(true); setMessage("");
    try {
      const selected = await api<ProjectState | null>("/api/projects/select", { method: "POST" });
      if (selected) { setProject(selected); setMessage("プロジェクトを開きました"); }
    } catch (error) { setMessage(error instanceof Error ? error.message : "操作に失敗しました"); }
    finally { setBusy(false); }
  };
  const createProject = (event: FormEvent) => { event.preventDefault(); void perform(() => api("/api/projects/create", { method: "POST", body: JSON.stringify({ rootPath: newRoot, name: newName, datasets: [] }) }), "プロジェクトを作成しました"); };
  const addDataset = (event: FormEvent) => { event.preventDefault(); void perform(() => api("/api/projects/current/datasets", { method: "POST", body: JSON.stringify({ key: dataset.key, name: dataset.name, repeats: dataset.repeats, triggerTags: splitTags(dataset.tags), removedTags: [] }) }), "データセットを追加しました"); };
  const saveProject = () => { if (project) void perform(() => api("/api/projects/current", { method: "PUT", body: JSON.stringify(project.config) }), "設定を保存しました"); };
  const updateDataset = (index: number, patch: Partial<Dataset>) => {
    if (!project) return;
    const datasets = project.config.datasets.map((item, i) => i === index ? { ...item, ...patch } : item);
    setProject({ ...project, config: { ...project.config, datasets } });
  };
  return <main className="shell">
    <header className="header"><div><p className="eyebrow">LOCAL WORKFLOW CONSOLE</p><h1>LoRA Maker</h1></div><div className={`connection connection--${connection}`}><span />FastAPI {labels[connection]} <button onClick={() => void checkHealth()}>再確認</button></div></header>
    {message && <p className="message" role="status">{message}</p>}
    <section className="panel"><div className="panel-heading"><div><h2>プロジェクト</h2><p className="hint">設定JSONをWindowsのファイル選択画面から選択します。</p></div><button type="button" onClick={() => void selectProject()} disabled={busy}>プロジェクトを開く</button></div>
      {!project && <form className="form-grid" onSubmit={createProject}><h3>新規作成</h3><label>作成先フォルダ<input value={newRoot} onChange={(e) => setNewRoot(e.target.value)} required /></label><label>キャラクター名<input value={newName} onChange={(e) => setNewName(e.target.value)} required /></label><button disabled={busy}>作成</button></form>}
    </section>
    {project && <><section className="project-title"><div><span>現在のプロジェクト</span><h2>{project.config.project.name}</h2><code>{project.rootPath}</code></div><button onClick={saveProject} disabled={busy}>変更を保存</button></section>
      {project.warnings.length > 0 && <section className="warnings"><strong>フォルダ構成の警告</strong><ul>{project.warnings.map((w) => <li key={w}>{w}</li>)}</ul></section>}
      <section className="panel"><h2>データセット</h2><div className="dataset-list">{project.config.datasets.map((item, index) => <article className="dataset-card" key={item.key}><div className="dataset-key">{item.key}</div><label>表示名<input value={item.name} onChange={(e) => updateDataset(index, { name: e.target.value })} /></label><label>学習回数<input type="number" min="1" value={item.repeats} onChange={(e) => updateDataset(index, { repeats: Number(e.target.value) })} /></label><label>識別タグ<input value={item.triggerTags.join(", ")} onChange={(e) => updateDataset(index, { triggerTags: splitTags(e.target.value) })} /></label><label>削除対象タグ<input value={item.removedTags.join(", ")} onChange={(e) => updateDataset(index, { removedTags: splitTags(e.target.value) })} /></label></article>)}</div>
        <form className="add-form" onSubmit={addDataset}><h3>データセット追加</h3><input pattern="[a-z0-9_]+" placeholder="キー" value={dataset.key} onChange={(e) => setDataset({ ...dataset, key: e.target.value })} required /><input placeholder="表示名" value={dataset.name} onChange={(e) => setDataset({ ...dataset, name: e.target.value })} required /><input type="number" min="1" value={dataset.repeats} onChange={(e) => setDataset({ ...dataset, repeats: Number(e.target.value) })} required /><input placeholder="識別タグ（カンマ区切り）" value={dataset.tags} onChange={(e) => setDataset({ ...dataset, tags: e.target.value })} /><button disabled={busy}>追加</button></form>
      </section></>}
  </main>;
}
