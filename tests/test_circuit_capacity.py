"""Valid generated workloads exercise the actual API and Qiskit subprocess."""
import pytest
from fastapi.testclient import TestClient
from main import app

@pytest.fixture
def client():
    # No lifespan: Qiskit must not need RAG, Chroma or a model.
    return TestClient(app, raise_server_exceptions=False)

def source(fmt, lines=50, qubits=20):
    if fmt == "qasm":
        header = ['OPENQASM 2.0;', 'include "qelib1.inc";', f'qreg q[{qubits}];']
        ops = [f'rz(pi/7) q[{i % qubits}];' for i in range(lines-3)]
    else:
        header = ['from qiskit import QuantumCircuit', 'from math import pi', f'qc = QuantumCircuit({qubits})']
        ops = [f'qc.rz(pi/7, {i % qubits})' for i in range(lines-3)]
    return "\n".join(header + ops)

@pytest.mark.parametrize("fmt", ["qasm", "python"])
@pytest.mark.parametrize("lines", [50, 500, 2000])
def test_valid_capacity(client, fmt, lines):
    code = source(fmt, lines)
    assert len(code.splitlines()) == lines
    r = client.post('/qiskit/optimize', json={'code': code, 'format': fmt})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['original']['qubits'] == 20
    assert d['original']['total_operacoes'] == lines-3
    assert d['formato_detectado'] == fmt
    assert d['tempo_processamento_segundos'] >= 0
    assert d['equivalente'] is None
    assert d['circuito_otimizado_qasm'].startswith('OPENQASM 2.0;')

@pytest.mark.parametrize("fmt", ["qasm", "python"])
def test_five_thousand_operations(client, fmt):
    code = source(fmt, 3) + '\n' + ('h q;\n' if fmt == 'qasm' else 'qc.h(range(20))\n') * 250
    r = client.post('/qiskit/optimize', json={'code': code, 'format': fmt})
    assert r.status_code == 200, r.text
    assert r.json()['original']['total_operacoes'] == 5000

@pytest.mark.parametrize("fmt", ["qasm", "python"])
def test_configurable_line_limit(client, monkeypatch, fmt):
    monkeypatch.setenv('MAX_CIRCUIT_LINES', '49')
    r = client.post('/qiskit/optimize', json={'code': source(fmt), 'format': fmt})
    assert r.status_code == 413, r.text
    assert r.json()['error']['code'] == 'CIRCUIT_LIMIT_EXCEEDED'
    assert r.json()['error']['details']['limit'] == 49
    monkeypatch.setenv('MAX_CIRCUIT_LINES', '50')
    assert client.post('/qiskit/optimize', json={'code': source(fmt), 'format': fmt}).status_code == 200

@pytest.mark.parametrize('body', [{}, {'qasm': 'x', 'code': 'x'}, {'qasm': 'x', 'optimization_level': True}, {'code':'x','format':'other'}])
def test_request_shape(client, body):
    r = client.post('/qiskit/optimize', json=body)
    assert r.status_code == 422
    assert r.json()['error']['code'] == 'INVALID_REQUEST'
    assert r.json()['error']['details']['stage'] == 'request'

def test_invalid_json(client):
    r = client.post('/qiskit/optimize', content='{', headers={'content-type':'application/json'})
    assert r.status_code == 400
    assert r.json()['error']['code'] == 'INVALID_JSON'

@pytest.mark.parametrize('fmt,code', [('qasm', 'OPENQASM 2.0;\nqreg q[1];\nx q[2];'), ('python','from qiskit import QuantumCircuit\nqc = QuantumCircuit(')])
def test_diagnostics(client, fmt, code):
    r = client.post('/qiskit/optimize', json={'code': code, 'format': fmt})
    assert r.status_code == 422
    assert r.json()['error']['code'] == 'INVALID_CIRCUIT'
    assert r.json()['error']['details']['line'] >= 1

@pytest.mark.parametrize('code', ['import os\nos.system("id")', '__import__("os")', 'open("/etc/passwd").read()', 'from qiskit import QuantumCircuit\nqc=QuantumCircuit(1)\nqc.__class__', 'while True: pass', 'from qiskit import QuantumCircuit\nqc=QuantumCircuit(1)\nqc.h([0]*1000000000)'])
def test_python_restricted(client, code):
    r = client.post('/qiskit/optimize', json={'code':code,'format':'python'})
    assert r.status_code == 422, r.text
    assert r.json()['error']['code'] == 'UNSUPPORTED_PYTHON'

def test_legacy_and_small_equivalence(client):
    code='OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; h q[0]; h q[0]; cx q[0],q[1]; cx q[0],q[1];'
    d=client.post('/qiskit/optimize',json={'qasm':code}).json()
    assert d['equivalente'] is True
    assert d['proposta']['total_operacoes'] == 0

def test_python_loop_registers_and_measure(client):
    code = 'from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister\nq=QuantumRegister(20,"q")\nc=ClassicalRegister(20,"c")\nqc=QuantumCircuit(q,c)\nfor i in range(20):\n    qc.h(q[i])\nqc.barrier()\nqc.measure(q,c)'
    r=client.post('/qiskit/optimize',json={'code':code,'format':'python'})
    assert r.status_code == 200, r.text
    assert r.json()['original']['total_operacoes'] == 40
    assert r.json()['original']['tipos_operacoes']['measure'] == 20
    assert r.json()['equivalente'] is None

def test_timeout_and_recovery(client, monkeypatch):
    monkeypatch.setenv('OPTIMIZATION_TIMEOUT_SECONDS','0.001')
    r=client.post('/qiskit/optimize',json={'qasm':source('qasm')})
    assert r.status_code == 504
    assert r.json()['error']['code'] == 'OPTIMIZATION_TIMEOUT'
    monkeypatch.setenv('OPTIMIZATION_TIMEOUT_SECONDS','60')
    assert client.post('/qiskit/optimize',json={'qasm':source('qasm')}).status_code == 200

@pytest.mark.parametrize('fmt', ['qasm', 'python'])
@pytest.mark.parametrize('setting,value', [('MAX_QUBITS','19'), ('MAX_CIRCUIT_GATES','46'), ('MAX_CIRCUIT_DEPTH','2')])
def test_structural_limits(client, monkeypatch, fmt, setting, value):
    monkeypatch.setenv(setting, value)
    r = client.post('/qiskit/optimize', json={'code':source(fmt), 'format':fmt})
    assert r.status_code == 413, r.text
    assert r.json()['error']['code'] == 'CIRCUIT_LIMIT_EXCEEDED'

def test_utf8_byte_limit_and_transport(client, monkeypatch):
    code = source('qasm') + '\n// ação'
    monkeypatch.setenv('MAX_CODE_SIZE_BYTES', str(len(code.encode())-1))
    r = client.post('/qiskit/optimize',json={'qasm':code})
    assert r.status_code == 413
    assert r.json()['error']['details']['resource'] == 'code_size_bytes'
    monkeypatch.setenv('MAX_CODE_SIZE_BYTES',str(len(code.encode())))
    assert client.post('/qiskit/optimize',json={'qasm':code}).status_code == 200
    monkeypatch.setenv('MAX_CODE_SIZE_BYTES', '10')
    r = client.post('/qiskit/optimize',content=b' '*17000,headers={'content-type':'application/json'})
    assert r.status_code == 413
    assert r.json()['error']['code'] == 'REQUEST_TOO_LARGE'

def test_classical_control_and_barriers(client):
    code='OPENQASM 2.0; include "qelib1.inc"; qreg a[1]; qreg b[1]; creg c[1]; h a[0]; barrier a,b; measure a[0] -> c[0]; if(c==1) x b[0];'
    r=client.post('/qiskit/optimize',json={'qasm':code})
    assert r.status_code == 200, r.text
    from qiskit import qasm2
    q=qasm2.loads(r.json()['circuito_otimizado_qasm'])
    assert q.num_qubits == 2 and q.num_clbits == 1
    assert q.count_ops()['measure'] == 1 and q.count_ops()['barrier'] == 1
    assert q.count_ops()['if_else'] == 1
    assert q.data[-1].operation.condition[1] == 1
    assert q.data[-1].operation.blocks[0].count_ops()['x'] == 1
    assert r.json()['equivalente'] is None

@pytest.mark.parametrize('fmt', ['qasm', 'python'])
def test_gate_coverage_equivalence(client, fmt):
    from qiskit import QuantumCircuit, qasm2
    from qiskit.quantum_info import Operator
    from math import pi
    operations = [('h',(0,)), ('x',(1,)), ('y',(2,)), ('z',(0,)), ('rx',(pi/7,0)), ('ry',(pi/8,1)),
                  ('rz',(pi/9,2)), ('cx',(0,1)), ('cz',(1,2)), ('ccx',(0,1,2)), ('swap',(0,2))]
    expected = QuantumCircuit(3)
    for name,args in operations:
        getattr(expected,name)(*args)
    code = qasm2.dumps(expected) if fmt == 'qasm' else 'from qiskit import QuantumCircuit\nqc=QuantumCircuit(3)\n' + '\n'.join(f'qc.{name}({",".join(map(str,args))})' for name,args in operations)
    r=client.post('/qiskit/optimize',json={'code':code,'format':fmt})
    assert r.status_code == 200, r.text
    actual=qasm2.loads(r.json()['circuito_otimizado_qasm'],custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    assert Operator(expected).equiv(Operator(actual))
    assert actual.size() == r.json()['proposta']['total_operacoes']
    assert actual.depth() == r.json()['proposta']['profundidade']

def test_custom_gate_definition_semantics(client):
    # A legal user-defined gate must not be replaced by a same-named legacy intrinsic.
    from qiskit import qasm2
    from qiskit.quantum_info import Operator
    code='OPENQASM 2.0; include "qelib1.inc"; gate rxx(t) a,b { x a; } qreg q[2]; rxx(0.2) q[0],q[1];'
    expected=qasm2.loads(code)
    r=client.post('/qiskit/optimize',json={'qasm':code})
    assert r.status_code == 200, r.text
    actual=qasm2.loads(r.json()['circuito_otimizado_qasm'],custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    assert Operator(expected).equiv(Operator(actual))

def test_custom_gate_expansion_is_bounded(client, monkeypatch):
    monkeypatch.setenv('MAX_CIRCUIT_GATES','10')
    code='OPENQASM 2.0; gate one a { U(0,0,0) a; U(0,0,0) a; } gate two a { one a; one a; } gate four a { two a; two a; } qreg q[1]; four q[0]; four q[0];'
    r=client.post('/qiskit/optimize',json={'qasm':code})
    assert r.status_code == 413, r.text

def test_python_step_budget(client, monkeypatch):
    monkeypatch.setenv('MAX_PYTHON_STEPS','1000')
    code='from qiskit import QuantumCircuit\nqc=QuantumCircuit(1)\nfor i in range(100):\n    for j in range(100):\n        a = 1'
    r=client.post('/qiskit/optimize',json={'code':code,'format':'python'})
    assert r.status_code == 413, r.text

def test_python_bad_index_is_domain_error(client):
    code='from qiskit import QuantumCircuit\nqc=QuantumCircuit(1)\nqc.h(2)'
    r=client.post('/qiskit/optimize',json={'code':code,'format':'python'})
    assert r.status_code == 422, r.text
    assert r.json()['error']['details']['line'] == 3

def test_actual_worker_cancellation_reaps_process_and_overload(monkeypatch):
    import asyncio
    import httpx
    import quantum.api as api
    async def scenario():
        started = asyncio.Event()
        processes = []
        create = asyncio.create_subprocess_exec
        async def spawn(*args, **kwargs):
            process = await create(*args, **kwargs)
            processes.append(process)
            started.set()
            return process
        monkeypatch.setattr(api.asyncio, 'create_subprocess_exec', spawn)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
            first = asyncio.create_task(c.post('/qiskit/optimize',json={'qasm':source('qasm',2000)}))
            await asyncio.wait_for(started.wait(),5)
            busy = await c.post('/qiskit/optimize',json={'qasm':source('qasm')})
            assert busy.status_code == 429
            assert busy.json()['error']['code'] == 'OPTIMIZER_BUSY'
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert processes[0].returncode is not None
            assert api._active == 0
    asyncio.run(scenario())

@pytest.mark.parametrize('path', ['/etc//passwd', '/etc/passwd', '/tmp/a\nb', 'other.inc'])
def test_includes_cannot_bypass_whitelist(client, path):
    r = client.post('/qiskit/optimize', json={'qasm':f'OPENQASM 2.0; include "{path}";'})
    assert r.status_code == 422, r.text
    assert r.json()['error']['details']['reason'] == 'Include externo não permitido.'

def test_commented_declaration_cannot_bypass_qubit_budget(client):
    r = client.post('/qiskit/optimize', json={'qasm':'OPENQASM 2.0; qreg// comment\nq[33];'})
    assert r.status_code == 413
    assert r.json()['error']['details']['resource'] == 'qubits'

def test_custom_definition_depth_limit(client, monkeypatch):
    monkeypatch.setenv('MAX_CIRCUIT_DEPTH','3')
    code='OPENQASM 2.0; gate foo a { U(0,0,0) a; U(0,0,0) a; } qreg q[1]; foo q[0]; foo q[0];'
    r=client.post('/qiskit/optimize',json={'qasm':code})
    assert r.status_code == 413
    assert r.json()['error']['details']['resource'] == 'circuit_depth'

def test_opaque_gate_is_not_interpreted_as_same_named_builtin(client):
    code='OPENQASM 2.0; opaque rxx(theta) a,b; qreg q[20]; rxx(0.5) q[0],q[1];'
    r=client.post('/qiskit/optimize',json={'qasm':code})
    assert r.status_code == 422, r.text
    assert r.json()['error']['code'] == 'UNSUPPORTED_OPERATION'

@pytest.mark.parametrize('digits', ['9'*5000, '0'*5000+'1'], ids=['overflow', 'leading-zeros'])
def test_overflow_register_diagnostic(client, digits):
    code='OPENQASM 2.0; qreg q[' + digits + '];'
    r=client.post('/qiskit/optimize',json={'qasm':code})
    assert r.status_code == 422, r.text
    assert r.json()['error']['code'] == 'INVALID_CIRCUIT'

@pytest.mark.parametrize('fmt', ['qasm','python'])
def test_capacity_above_default_is_configurable(client, monkeypatch, fmt):
    monkeypatch.setenv('MAX_CIRCUIT_LINES','3000')
    monkeypatch.setenv('MAX_QUBITS','40')
    r=client.post('/qiskit/optimize',json={'code':source(fmt,3000,33),'format':fmt})
    assert r.status_code == 200, r.text
    assert r.json()['original']['qubits'] == 33
    assert r.json()['original']['total_operacoes'] == 2997

def test_http_disconnect_terminates_worker(monkeypatch):
    import asyncio
    import json
    import socket
    import sys
    import threading
    import time
    import uvicorn
    import quantum.api as api
    started = threading.Event()
    children = []
    real_spawn = asyncio.create_subprocess_exec
    async def spawn(*args, **kwargs):
        # Real long-running OS process: prove disconnect kills it, not normal completion.
        process = await real_spawn(sys.executable, '-c', 'import time; time.sleep(30)', **kwargs)
        children.append(process)
        started.set()
        return process
    monkeypatch.setattr(api.asyncio, 'create_subprocess_exec', spawn)
    server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='error'))
    listener = socket.socket()
    listener.bind(('127.0.0.1',0))
    port = listener.getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={'sockets':[listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        body = json.dumps({'qasm':source('qasm')}).encode()
        connection = socket.create_connection(('127.0.0.1',port))
        connection.sendall(b'POST /qiskit/optimize HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
        assert started.wait(5)
        connection.close()
        deadline = time.monotonic() + 3
        while (children[0].returncode is None or api._active) and time.monotonic() < deadline:
            time.sleep(.01)
        assert children[0].returncode is not None, 'Disconnected client left its worker alive'
        assert api._active == 0
    finally:
        server.should_exit = True
        server.force_exit = True
        # A failing regression test must not leave its child asleep for 30 seconds.
        for process in children:
            if process.returncode is None:
                process.kill()
        thread.join(5)
        listener.close()
