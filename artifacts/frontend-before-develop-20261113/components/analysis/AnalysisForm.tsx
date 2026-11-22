"use client";
import {useState, useRef, type FormEvent} from "react";
import {api, type Result} from "../../services/analysis-api";
import LoadingView from "./LoadingView";
import ResultsView from "./ResultsView";
import AnalysisFeedback from "./AnalysisFeedback";
import SettingsPanel from "./SettingsPanel";
export default function AnalysisForm(){
 const generation=useRef(0);
 const [errorStatus,setErrorStatus]=useState(0);
 const [requestId,setRequestId]=useState("");
 const [editing,setEditing]=useState(true);
 const [code,setCode]=useState("");
 const [question,setQuestion]=useState("");
 const [mode,setMode]=useState("query");
 const [level,setLevel]=useState(3);
 const [topK,setTopK]=useState(3);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState("");
 const [result,setResult]=useState<Result|null>(null);
 const [documents,setDocuments]=useState<{id:string;filename:string}[]>([]);
 const [listed,setListed]=useState(false);
 async function list(){try{const d=await api<{documents:{id:string;filename:string}[]}>("/documents");setDocuments(d.documents);setListed(true);}catch(e){setError((e as Error).message);}}
 async function upload(file?:File){if(!file)return;setBusy(true);setError("");try{await api("/documents",undefined,file);await list();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function analyze(e?:FormEvent){e?.preventDefault(); const current=++generation.current; setEditing(false);setError("");setResult(null);setBusy(true);
 function accept(data:Result){if(current===generation.current)setResult(data);}
 try{
 if(mode==="qiskit"){if(!code.trim())throw new Error("Informe um circuito OpenQASM 2.");accept(await api<Result>("/qiskit/optimize",{qasm:code,optimization_level:level}));}
 else {const text=[question,code].filter(Boolean).join("\n\n");if(!text.trim())throw new Error("Informe a pergunta ou o código.");
 accept(await api<Result>(mode==="search"?"/search":"/query",mode==="search"?{query:text,top_k:topK}:{input:text,top_k:topK}));}
 }catch(e){if(current===generation.current){setError((e as Error).message);setErrorStatus((e as {status?:number}).status||0);setRequestId((e as {requestId?:string}).requestId||"");}}finally{if(current===generation.current)setBusy(false);}}
 const back=()=>{generation.current++;setBusy(false);setEditing(true);setError("");setResult(null);};
 return <><header className="topbar"><div className="topbar-inner"><div className="brand"><div className="brand-symbol">Q</div><div className="brand-copy"><strong>GrowUp · Agente RAG</strong><span>Documentação e otimização de circuitos</span></div></div><a href="http://localhost:8000/docs" target="_blank" rel="noreferrer">API</a></div></header>
 <main className="workspace"><section className="page-intro"><span className="eyebrow">INTELIGÊNCIA PARA COMPUTAÇÃO QUÂNTICA</span><h1>Olá, programador.</h1><p>Consulte documentação, peça uma explicação ao Gemini ou otimize um circuito com Qiskit.</p></section>
 {editing&&<form className="analysis-grid" onSubmit={analyze}><section className="panel"><div className="panel-heading"><h2>{mode==="qiskit"?"Circuito OpenQASM 2":"Código ou contexto (opcional)"}</h2></div>
 <textarea aria-label="Código" className="question-input" style={{minHeight:300,fontFamily:"monospace"}} value={code} maxLength={mode==="qiskit"?10000:20000} onChange={e=>setCode(e.target.value)}/>
 {mode==="qiskit"&&<button type="button" className="button-secondary" onClick={()=>setCode('OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; h q[0]; h q[0];')}>Carregar circuito de exemplo</button>}
 <div className="toolbar-actions"><button type="button" className="button-secondary" onClick={async()=>{try{await navigator.clipboard.writeText(code);}catch{setError("Não foi possível copiar. Use Ctrl+C.");}}}>Copiar código</button><button type="button" className="button-secondary" onClick={async()=>{try{setCode(await navigator.clipboard.readText());}catch{setError("Não foi possível colar. Use Ctrl+V.");}}}>Colar código</button><button type="button" className="button-secondary" disabled={busy} onClick={()=>{setCode("");setQuestion("");setResult(null);setError("");}}>Limpar tudo</button></div><p>Python pode ser enviado para explicação. A otimização executável aceita OpenQASM 2.</p></section>
 <section className="panel side-panel"><label>Operação<select aria-label="Operação" className="question-input" value={mode} onChange={e=>{setMode(e.target.value);setResult(null);setError("");}}><option value="query">Explicar com Gemini + RAG</option><option value="search">Buscar documentação</option><option value="qiskit">Otimizar com Qiskit</option></select></label>
 {mode!=="qiskit"?<label>Pergunta<textarea aria-label="Pergunta" className="question-input" value={question} maxLength={mode==="search"?2000:9000} onChange={e=>setQuestion(e.target.value)}/></label>:<label>Nível de otimização<select aria-label="Nível" value={level} onChange={e=>setLevel(Number(e.target.value))}>{[0,1,2,3].map(n=><option key={n}>{n}</option>)}</select></label>}
 <SettingsPanel backend="reference" topK={String(topK)} onBackendChange={()=>{}} onTopKChange={v=>setTopK(Number(v))}/><button className="button-primary" disabled={busy}>{busy?"Processando…":"Analisar"}</button>
 <p>As explicações enviam a pergunta e os trechos recuperados ao Gemini.</p></section></form>}
 {!editing&&busy&&<LoadingView mode={mode} onBack={back}/>}
 {error&&<AnalysisFeedback type={errorStatus===422?"invalid-code":"service-error"} message={error} requestId={requestId} onBack={back} onRetry={()=>void analyze()}/>}
 {result?.status==="insufficient_context"?<AnalysisFeedback type="insufficient-evidence" message="A busca não encontrou documentação suficiente. Reformule a pergunta ou envie documentos." requestId={result.request_id} onBack={back} onRetry={()=>void analyze()}/>:result&&<ResultsView result={result} code={code} question={question} onBack={back}/>}
 <section className="panel result-section"><h2>Documentos da base</h2><p>Arquivos UTF-8 .txt, .md, .py ou .qasm; até 256 KiB.</p><input aria-label="Enviar documento" type="file" accept=".txt,.md,.py,.qasm" disabled={busy} onChange={e=>{void upload(e.target.files?.[0]);e.target.value="";}}/><button type="button" className="button-secondary" disabled={busy} onClick={()=>void list()}>Atualizar lista</button>{listed&&<ul>{documents.length?documents.map(d=><li key={d.id}>{d.filename}</li>):<li>Nenhum documento indexado.</li>}</ul>}</section>
 </main></>;
}
