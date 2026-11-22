"""Read-only HTTP smoke checks against a running API or the Next /api proxy.

Usage: python scripts/verificar_circuitos_http.py --base-url http://127.0.0.1:3000/api
No LLM calls, document uploads or changes to the vector database.
"""
import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def generated(fmt, lines):
    if fmt == "qasm":
        return '\n'.join(['OPENQASM 2.0;', 'include "qelib1.inc";', 'qreg q[20];'] +
                         [f'rz(pi/7) q[{i % 20}];' for i in range(lines - 3)])
    return '\n'.join(['from qiskit import QuantumCircuit', 'from math import pi', 'qc=QuantumCircuit(20)'] +
                     [f'qc.rz(pi/7, {i % 20})' for i in range(lines - 3)])


def run(base):
    records = []
    with urllib.request.urlopen(base + '/qiskit/limits', timeout=10) as response:
        limits = json.load(response)
    def post(label, body, expected, raw=False):
        request = urllib.request.Request(base + '/qiskit/optimize',
            data=body if raw else json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        start = time.perf_counter()
        try:
            response = urllib.request.urlopen(request, timeout=limits['optimization_timeout_seconds'] + 20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            data = json.load(response)
            assert response.status == expected, (label, response.status, data)
            record = {'case': label, 'status': response.status, 'http_seconds': round(time.perf_counter()-start, 3)}
        if expected == 200:
            record.update(original=data['original'], proposal=data['proposta'], worker_seconds=data['tempo_processamento_segundos'])
            assert data['original']['qubits'] == 20
            assert data['equivalente'] is None
            assert data['circuito_otimizado_qasm'].startswith('OPENQASM 2.0;')
        else:
            record['code'] = data['error']['code']
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
        return data
    for fmt in ('qasm', 'python'):
        for lines in (50, 500, 2000):
            result = post(f'{fmt}-{lines}-lines', {'code':generated(fmt, lines),'format':fmt}, 200)
            assert result['original']['total_operacoes'] == lines - 3
        broadcast = generated(fmt, 3) + '\n' + ('h q;\n' if fmt == 'qasm' else 'qc.h(range(20))\n') * 250
        result = post(f'{fmt}-5000-operations', {'code':broadcast,'format':fmt}, 200)
        assert result['original']['total_operacoes'] == 5000
    post('legacy-qasm-2000-lines', {'qasm':generated('qasm',2000)}, 200)
    post('line-limit', {'qasm':generated('qasm',limits['max_circuit_lines']+1)}, 413)
    post('invalid-qasm', {'qasm':'OPENQASM 2.0; qreg q[1]; bad q[0];'}, 422)
    post('unsafe-python', {'code':'import os\nos.system("id")','format':'python'}, 422)
    post('invalid-shape', {}, 422)
    post('invalid-json', b'{', 400, raw=True)
    return {'base_url':base, 'limits':limits, 'cases':records}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--report')
    args = parser.parse_args()
    report = run(args.base_url.rstrip('/'))
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
