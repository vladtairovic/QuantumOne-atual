"""One environment contract for the API, parsers, workers and browser."""
import math
import os
from dataclasses import asdict, dataclass

from .errors import CircuitError, enforce_limit


def positive(name, default, cast=int):
    value = cast(os.getenv(name, str(default)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} deve ser positivo e finito.")
    return value


@dataclass(frozen=True)
class Limits:
    max_circuit_lines: int
    max_qubits: int
    max_classical_bits: int
    max_circuit_gates: int
    max_circuit_depth: int
    max_code_size_bytes: int
    optimization_timeout_seconds: float
    max_concurrent_optimizations: int
    worker_memory_mb: int
    max_python_steps: int
    max_equivalence_qubits: int
    max_equivalence_operations: int

    @property
    def max_request_bytes(self):
        # JSON may encode each source byte as a six-byte Unicode escape.
        return self.max_code_size_bytes * 6 + 16384

    def public(self):
        return {**asdict(self), "max_request_bytes": self.max_request_bytes}


def get_limits():
    return Limits(
        positive("MAX_CIRCUIT_LINES", 2500), positive("MAX_QUBITS", 32),
        positive("MAX_CLASSICAL_BITS", 256), positive("MAX_CIRCUIT_GATES", 10000),
        positive("MAX_CIRCUIT_DEPTH", 10000), positive("MAX_CODE_SIZE_BYTES", 524288),
        positive("OPTIMIZATION_TIMEOUT_SECONDS", os.getenv("QISKIT_TIMEOUT_SECONDS", "60"), float),
        positive("MAX_CONCURRENT_OPTIMIZATIONS", 1), positive("QISKIT_WORKER_MEMORY_MB", 1536),
        positive("MAX_PYTHON_STEPS", 200000), positive("MAX_EQUIVALENCE_QUBITS", 8),
        positive("MAX_EQUIVALENCE_OPERATIONS", 1000),
    )


def validate_source(code, limits=None):
    limits = limits or get_limits()
    if not isinstance(code, str) or not code.strip():
        raise CircuitError("INVALID_CIRCUIT", "O código não pode estar vazio.")
    try:
        size = len(code.encode("utf-8"))
    except UnicodeError as exc:
        raise CircuitError("INVALID_CIRCUIT", "O código deve conter texto UTF-8 válido.") from exc
    enforce_limit("code_size_bytes", size, limits.max_code_size_bytes)
    enforce_limit("circuit_lines", len(code.splitlines()), limits.max_circuit_lines)
    return limits
