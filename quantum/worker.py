"""Disposable worker: OS budgets before importing Qiskit; no user Python execution."""
import json
import math
import sys
from .config import get_limits
from .errors import CircuitError

def resource_limits(limits):
    if sys.platform == "linux":
        import resource
        import tempfile
        # dill (imported by Qiskit) queries this directory during import.
        # Resolve it before denying file writes; user source is never executed.
        tempfile.gettempdir()
        memory = limits.worker_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        cpu = math.ceil(limits.optimization_timeout_seconds)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

def main():
    limits = get_limits()
    resource_limits(limits)
    try:
        from .qiskit_service import processar_circuito
        entrada = json.load(sys.stdin)
        result = processar_circuito(entrada["code"], entrada["format"], entrada["optimization_level"])
        response = json.dumps({"result": result})
        if len(response.encode()) > limits.max_request_bytes:
            raise CircuitError("OUTPUT_LIMIT_EXCEEDED", "O resultado excede o orçamento de saída.", status=413)
    except CircuitError as exc:
        response = json.dumps({"error": exc.payload(), "status": exc.status})
    except MemoryError:
        response = json.dumps({"error": {"code": "WORKER_RESOURCE_ERROR", "message": "Orçamento de memória excedido.", "details": {}}, "status": 503})
    except Exception:
        response = json.dumps({"error": {"code": "OPTIMIZATION_FAILED", "message": "Não foi possível concluir a otimização Qiskit.", "details": {}}, "status": 500})
    print(response)

if __name__ == "__main__":
    main()
