import pytest

from quantum.errors import CircuitError
from quantum.python_parser import carregar_circuito_python
from quantum.qiskit_service import carregar_circuito_qasm


def test_qasm_index_diagnostic_explains_declared_range():
    code = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[10];\nh q[12];'
    with pytest.raises(CircuitError) as result:
        carregar_circuito_qasm(code)
    error = result.value
    assert error.code == 'INVALID_CIRCUIT'
    assert error.details['line'] == 4
    assert 'q[12]' in error.details['reason']
    assert '0 a 9' in error.details['reason']
    assert '10' in error.details['reason']


@pytest.mark.parametrize('statement,name', [
    ('import numpy as np','numpy'),
    ('from qiskit import transpile','qiskit.transpile'),
    ('from qiskit_aer import AerSimulator','qiskit_aer.AerSimulator'),
])
def test_python_import_diagnostic_identifies_unsupported_symbol(statement, name):
    with pytest.raises(CircuitError) as result:
        carregar_circuito_python('from qiskit import QuantumCircuit\n' + statement)
    error = result.value
    assert error.code == 'UNSUPPORTED_PYTHON'
    assert error.details['line'] == 2
    assert name in error.details['reason']
    assert 'QuantumCircuit' in error.details['reason']


def test_parameter_explains_symbolic_limitation():
    with pytest.raises(CircuitError) as result:
        carregar_circuito_python('from qiskit import QuantumCircuit\nfrom qiskit.circuit import Parameter')
    assert result.value.details['line'] == 2
    assert 'ângulos numéricos' in result.value.details['reason']
    assert 'Parameter' in result.value.details['reason']
