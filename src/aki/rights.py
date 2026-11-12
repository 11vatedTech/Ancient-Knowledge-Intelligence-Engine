"""Fail-closed rights policy."""
from .domain import ProcessingOperation, RightsGrant


class RightsDenied(PermissionError):
    """Raised when the recorded grant does not authorize an operation."""


def require_permission(grant: RightsGrant, operation: ProcessingOperation) -> None:
    if not grant.permits(operation):
        raise RightsDenied(
            f"Operation '{operation.value}' is not authorized by rights status "
            f"'{grant.status.value}'. Verify and record permission before proceeding."
        )
