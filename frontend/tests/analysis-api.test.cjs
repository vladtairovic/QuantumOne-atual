const { test } = require('node:test');
const assert = require('node:assert/strict');
const { circuitContext, optimizeCircuit, diagnoseCode, getCircuitLimits, analisar, obterProgresso, AnalysisError } = require('/tmp/analysis-api-test/analysis-api.js');

const corpoAnalise = {
  mensagem: 'Explique o circuito.', codigo: 'OPENQASM 2.0;', formato: 'qasm',
  arquivos: [{nome: 'projeto/src/c.py', conteudo: 'from qiskit import QuantumCircuit'}],
  conversation_id: null, progress_id: '7f0c5d1e-8a4b-4c6d-9e2f-1a2b3c4d5e6f', perfil: 'avancado', top_k: 8, pesquisa_web: true,
};

test('analisar posts the unified body to /api/analise without a client timeout', async () => {
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({url, method: options.method, headers: options.headers, body: JSON.parse(options.body), signal: options.signal});
    return new Response(JSON.stringify({request_id: 'r1', resposta: 'Resposta.', status: 'ok', answer_mode: 'llm', sources: [],
      circuitos: [], arquivos: [], warnings: [], etapas: [], tempo_total_segundos: 1.5, conversation_id: null}),
      {headers: {'x-request-id': 'cabecalho'}});
  };
  const controller = new AbortController();
  const resposta = await analisar(corpoAnalise, controller.signal);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/analise');
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].headers['Content-Type'], 'application/json');
  assert.deepEqual(calls[0].body, corpoAnalise);
  assert.equal(calls[0].signal, controller.signal);
  assert.equal(resposta.request_id, 'r1');
  assert.equal(resposta.resposta, 'Resposta.');
});

test('analisar falls back to the x-request-id header and surfaces API errors', async () => {
  global.fetch = async () => new Response(JSON.stringify({resposta: 'ok', status: 'ok', answer_mode: 'local', sources: []}),
    {headers: {'x-request-id': 'cabecalho'}});
  assert.equal((await analisar(corpoAnalise)).request_id, 'cabecalho');
  global.fetch = async () => new Response(JSON.stringify({request_id: 'e1', code: 'VALIDATION_ERROR', message: 'Envie uma mensagem, um código ou arquivos.', fields: ['mensagem']}), {status: 422});
  await assert.rejects(analisar({...corpoAnalise, mensagem: '', codigo: null, arquivos: []}), e =>
    e instanceof AnalysisError && e.status === 422 && e.code === 'VALIDATION_ERROR' && e.requestId === 'e1' &&
    e.message === 'Envie uma mensagem, um código ou arquivos.');
});

test('obterProgresso reads live progress without cache', async () => {
  const progresso = {progress_id: corpoAnalise.progress_id, concluido: false, etapa: 'Consultando nvidia/modelo (1/7)',
    modelo: 'nvidia/modelo', segundos_na_etapa: 2.5, segundos_total: 9, historico: [{texto: 'Base local consultada', segundos: 1.2}],
    parcial: 'O circuito tem dois erros'};
  global.fetch = async (url, options) => {
    assert.equal(url, '/api/analise/progresso/' + corpoAnalise.progress_id);
    assert.equal(options.cache, 'no-store');
    return new Response(JSON.stringify(progresso));
  };
  assert.deepEqual(await obterProgresso(corpoAnalise.progress_id, new AbortController().signal), progresso);
  // Sem texto parcial (fora da redação, ou API antiga), o campo vem como null.
  const semParcial = { ...progresso };
  delete semParcial.parcial;
  global.fetch = async () => new Response(JSON.stringify({...semParcial, parcial: ''}));
  assert.equal((await obterProgresso(corpoAnalise.progress_id)).parcial, null);
  global.fetch = async () => new Response(JSON.stringify(semParcial));
  assert.equal((await obterProgresso(corpoAnalise.progress_id)).parcial, null);
});

test('obterProgresso returns null on 404, server errors and network failures', async () => {
  global.fetch = async () => new Response(JSON.stringify({code: 'NOT_FOUND', message: 'Progresso não encontrado.'}), {status: 404});
  assert.equal(await obterProgresso(corpoAnalise.progress_id), null);
  global.fetch = async () => new Response('', {status: 502});
  assert.equal(await obterProgresso(corpoAnalise.progress_id), null);
  global.fetch = async () => { throw new TypeError('Failed to fetch'); };
  assert.equal(await obterProgresso(corpoAnalise.progress_id), null);
  global.fetch = async () => new Response('não é JSON');
  assert.equal(await obterProgresso(corpoAnalise.progress_id), null);
});

test('large circuit context is bounded and preserves numeric metrics', () => {
  const code = 'h q[0];\n'.repeat(2000);
  const context = circuitContext(code, 'qasm', {
    original: {qubits:20,total_operacoes:2000,profundidade:100,tipos_operacoes:{veryLong:'x'.repeat(50000)}},
    proposta: {qubits:20,total_operacoes:2000,profundidade:90},
  });
  assert.ok(context.length < 9000);
  assert.ok(context.includes('Trecho central omitido'));
  assert.ok(context.includes('"operacoes":2000'));
  assert.ok(context.includes('"profundidade":90'));
  assert.ok(!context.includes('veryLong'));
});

test('Python reaches deterministic endpoint with explicit format and warnings', async () => {
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({url,body:JSON.parse(options.body)});
    return new Response(JSON.stringify({warnings:['Equivalência não verificada.'],formato_saida:'qasm'}),
      {headers:{'x-request-id':'test'}});
  };
  const code = 'from qiskit import QuantumCircuit\nqc=QuantumCircuit(20)';
  const result = await optimizeCircuit(code, new AbortController().signal, 'python');
  assert.deepEqual(calls, [{url:'/api/qiskit/optimize',body:{code,format:'python',optimization_level:3,perfil:'balanceado'}}]);
  assert.equal(result.request_id,'test');
  assert.deepEqual(result.warnings,['Equivalência não verificada.']);
});

test('diagnostics retain source line and reason', async () => {
  global.fetch = async () => new Response(JSON.stringify({request_id:'test',error:{message:'Código inválido.',details:{line:42,reason:'Índice inválido.'}}}),{status:422});
  await assert.rejects(optimizeCircuit('x',new AbortController().signal), e =>
    e instanceof AnalysisError && e.status === 422 && e.message.includes('Linha 42') && e.message.includes('Índice inválido.'));
});

test('limits come from server without browser cache', async () => {
  global.fetch = async (url, options) => {
    assert.equal(url,'/api/qiskit/limits');
    assert.equal(options.cache,'no-store');
    return new Response(JSON.stringify({max_circuit_lines:5000,optimization_timeout_seconds:180}));
  };
  assert.equal((await getCircuitLimits(new AbortController().signal)).optimization_timeout_seconds,180);
});

test('limits recover after the API starts without repeating optimization', async () => {
  let requests = 0;
  global.fetch = async () => ++requests === 1
    ? new Response('Internal Server Error', {status:500})
    : new Response(JSON.stringify({optimization_timeout_seconds:60}));
  assert.equal((await getCircuitLimits(new AbortController().signal)).optimization_timeout_seconds,60);
  assert.equal(requests,2);
});

test('limits recover from a network failure', async () => {
  let requests = 0;
  global.fetch = async () => {
    if (++requests === 1) throw new TypeError('Failed to fetch');
    return new Response(JSON.stringify({optimization_timeout_seconds:60}));
  };
  assert.equal((await getCircuitLimits(new AbortController().signal)).optimization_timeout_seconds,60);
});

test('abort during limits retry stops further requests', async () => {
  const controller = new AbortController();
  let requests = 0;
  global.fetch = async () => {
    requests++;
    setTimeout(() => controller.abort(),20);
    return new Response('', {status:503});
  };
  await assert.rejects(getCircuitLimits(controller.signal), e => e.name === 'AbortError');
  assert.equal(requests,1);
});

test('permanent limits errors are not retried', async () => {
  let requests = 0;
  global.fetch = async () => { requests++; return new Response('',{status:404}); };
  await assert.rejects(getCircuitLimits(new AbortController().signal), e => e.status === 404);
  assert.equal(requests,1);
});

test('persistent unavailability stops retrying and explains recovery', async (t) => {
  const realTimeout = global.setTimeout;
  t.mock.method(global, 'setTimeout', callback => realTimeout(callback,0));
  let requests = 0;
  global.fetch = async () => { requests++; return new Response('',{status:503}); };
  await assert.rejects(getCircuitLimits(new AbortController().signal), e =>
    e instanceof AnalysisError && e.status === 503 && e.message.includes('Aguarde e tente novamente'));
  assert.equal(requests,7);
});

test('diagnostics do not repeat the same message and reason', async () => {
  global.fetch = async () => new Response(JSON.stringify({error:{message:'Importação não permitida.',details:{line:2,reason:'Importação não permitida.'}}}),{status:422});
  await assert.rejects(optimizeCircuit('import os',new AbortController().signal,'python'), e =>
    e.message === 'Importação não permitida. Linha 2.');
});

test('optimization profile is forwarded and diagnosis uses its own endpoint', async () => {
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({url, body: JSON.parse(options.body)});
    const corpo = url.endsWith('/diagnose')
      ? {linguagem:'qasm', confianca_global:'baixa', problemas:[], codigo_corrigido:'OPENQASM 2.0;', validacao:{valido:true}, limitacoes:[]}
      : {warnings:[]};
    return new Response(JSON.stringify(corpo), {headers:{'x-request-id':'id'}});
  };
  await optimizeCircuit('OPENQASM 2.0;', new AbortController().signal, 'qasm', 'avancado');
  const diagnostico = await diagnoseCode('h q[9];', new AbortController().signal, 'qasm');
  assert.equal(calls[0].body.perfil, 'avancado');
  assert.deepEqual(calls[1], {url:'/api/qiskit/diagnose', body:{code:'h q[9];', format:'qasm'}});
  assert.equal(diagnostico.validacao.valido, true);
});
