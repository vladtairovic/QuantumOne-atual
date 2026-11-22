export type Source = {document_id:string; chunk_id:string; source:string; content:string};
export type Result = {response?:string; sources?:Source[]; chunks?:Source[]; status?:string; request_id?:string;
 original?:{profundidade:number;total_operacoes:number}; proposta?:{profundidade:number;total_operacoes:number};
 equivalente?:boolean|null; motivo?:string; circuito_otimizado_qasm?:string};
export async function api<T>(path:string, body?:unknown, file?:File):Promise<T> {
 const form = file ? new FormData() : undefined;
 if(file) form!.append("file", file);
 const response=await fetch("/api"+path, {method:body||file?"POST":"GET",
 headers:body?{"Content-Type":"application/json"}:undefined,
 body:form || (body?JSON.stringify(body):undefined), signal:AbortSignal.timeout(120000)});
 const data=await response.json().catch(()=>null);
 if(!response.ok) throw Object.assign(new Error(data?.message || "Falha ao acessar serviço ("+response.status+")."),{status:response.status,requestId:data?.request_id});
 return data;
}
