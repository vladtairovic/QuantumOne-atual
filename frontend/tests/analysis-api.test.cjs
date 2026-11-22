const { test } = require('node:test');
const assert = require('node:assert/strict');
const { circuitContext, optimizeCircuit, getCircuitLimits, AnalysisError } = require('/tmp/analysis-api-test/analysis-api.js');

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
  assert.deepEqual(calls, [{url:'/api/qiskit/optimize',body:{code,format:'python',optimization_level:3}}]);
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
