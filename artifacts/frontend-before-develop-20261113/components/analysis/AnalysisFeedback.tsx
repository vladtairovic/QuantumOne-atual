"use client";
export type AnalysisFeedbackType="invalid-code"|"insufficient-evidence"|"service-error";
export default function AnalysisFeedback({type,message,requestId,onBack,onRetry}:{type:AnalysisFeedbackType;message:string;requestId?:string;onBack:()=>void;onRetry:()=>void}){
return <section className="panel result-section" role="alert"><h2>{type==="invalid-code"?"Entrada inválida":type==="insufficient-evidence"?"Evidência insuficiente":"Serviço indisponível"}</h2><p>{message}</p>{requestId&&<p>Solicitação: <code>{requestId}</code></p>}<button className="button-secondary" onClick={onBack}>{type==="invalid-code"?"Voltar e corrigir código":type==="insufficient-evidence"?"Reformular pergunta":"Voltar à entrada"}</button><button className="button-primary" onClick={onRetry}>Tentar novamente</button></section>;
}
