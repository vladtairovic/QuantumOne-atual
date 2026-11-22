"""Typed failures shared by parsing, worker and HTTP transport."""


class CircuitError(ValueError):
    def __init__(self, code, message, details=None, status=422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status = status

    def payload(self):
        return {"code": self.code, "message": self.message, "details": self.details}


def enforce_limit(resource, actual, limit):
    if actual > limit:
        raise CircuitError(
            "CIRCUIT_LIMIT_EXCEEDED", f"Limite de {resource} excedido.",
            {"resource": resource, "actual": actual, "limit": limit, "stage": "domain"}, 413,
        )
