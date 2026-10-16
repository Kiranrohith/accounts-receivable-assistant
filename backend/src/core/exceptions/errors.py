"""Application exception hierarchy. The API error handler maps these to HTTP status."""


class AppError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(AppError):
    status_code = 401


class PermissionDeniedError(AppError):
    status_code = 403


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


class ValidationFailure(AppError):
    status_code = 422


class UploadRejected(AppError):
    """A file failed the upload gate (size, type, signature, page count)."""

    status_code = 422


class ExternalServiceError(AppError):
    """A service we depend on (ECB, an LLM, mail) failed or is unreachable."""

    status_code = 502
