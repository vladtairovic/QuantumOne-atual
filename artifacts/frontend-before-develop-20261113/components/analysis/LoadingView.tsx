"use client";
export default function LoadingView({mode,onBack}:{mode:string;onBack:()=>void}){
return <section className="panel result-section" role="status"><h2>Processando análise</h2><p>{mode==="qiskit"?"Transpilando circuito e verificando equivalência.":"Recuperando documentação e processando a solicitação."}</p><p>Aguarde a resposta do serviço. O tempo depende da operação.</p><button type="button" className="button-secondary" onClick={onBack}>Voltar à entrada</button></section>;
}
