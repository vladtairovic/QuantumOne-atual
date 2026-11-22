"use client";
import {useState} from "react";
import type {Result} from "../../services/analysis-api";
import SourceDrawer,{type SourceDetails} from "./SourceDrawer";
export default function ResultsView({result,code,question,onBack}:{result:Result;code:string;question:string;onBack:()=>void}){
const [source,setSource]=useState<SourceDetails|null>(null);
const [notice,setNotice]=useState("");
const sources=result.sources||result.chunks||[];
async function copy(){try{await navigator.clipboard.writeText(result.circuito_otimizado_qasm||result.response||"");setNotice("Resultado copiado.");}catch{setNotice("Use a seleção de texto e Ctrl+C para copiar.");}}
return <section className="panel result-section" aria-label="Resultado"><h2>Resultado da análise</h2><p>{question}</p><button className="button-secondary" onClick={onBack}>Editar entrada</button><button className="button-secondary" onClick={copy}>Copiar resultado</button><p role="status">{notice}</p>
{result.response&&<><h3>Explicação</h3><p style={{whiteSpace:"pre-wrap"}}>{result.response}</p></>}
{result.original&&result.proposta?<><h3>Métricas calculadas com Qiskit</h3><p>Operações: {result.original.total_operacoes} → {result.proposta.total_operacoes}. Profundidade: {result.original.profundidade} → {result.proposta.profundidade}.</p><p>Equivalência: {result.equivalente===true?"confirmada":result.equivalente===false?"não equivalente":"não verificada"}. {result.motivo}</p><div className="comparison-grid"><div><h3>Código original</h3><pre>{code}</pre></div><div><h3>Código otimizado</h3><pre>{result.circuito_otimizado_qasm}</pre></div></div></>:<p>Métricas científicas não calculadas nesta consulta. Use a operação Qiskit com OpenQASM 2.</p>}
{sources.length>0&&<><h3>Fontes recuperadas</h3>{sources.map((s,i)=><button className="source-item button-secondary" key={s.chunk_id} onClick={()=>setSource({id:s.chunk_id,title:s.source,origin:s.document_id,category:"Documentação recuperada",type:"documentation",excerpt:s.content,relationship:"Fonte fornecida como contexto. Fidelidade das citações não verificada automaticamente.",url:/^https?:\/\//.test(s.source)?s.source:null})}>Ver fonte {i+1}: {s.source}</button>)}</>}
<SourceDrawer source={source} onClose={()=>setSource(null)}/></section>;
}
