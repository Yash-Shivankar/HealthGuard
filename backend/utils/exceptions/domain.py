"""Exceptions raised by the service layer.

Services must not know about HTTP - they raise these, and the API exception
handler maps them onto status codes.  That is the boundary AUDIT 7.1 asks for:
"services never touch HTTP".
"""


class DomainError(Exception):
    """Base class for an expected, explainable failure.

    ``message`` is safe to show a user.  Anything that is not safe to show a
    user does not belong in a DomainError.
    """

    status_code = 400
    code = "domain_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class ValidationError(DomainError):
    status_code = 400
    code = "validation_error"


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConsentRequiredError(DomainError):
    """Raised when an action needs consent that was never given or was withdrawn.

    A distinct type because under the DPDP Act this is not an ordinary
    validation failure - it is the one the audit log has to be able to show
    (AUDIT 4.1).
    """

    status_code = 403
    code = "consent_required"


class ServiceUnavailableError(DomainError):
    status_code = 503
    code = "service_unavailable"
