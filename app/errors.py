class DomainError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra or {}

