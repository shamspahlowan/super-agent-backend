from fastapi import HTTPException, Request, status

from app.ledger.balance_engine import BalanceEngine


def get_balance_engine(
    request: Request,
) -> BalanceEngine:
    ledger = getattr(
        request.app.state,
        "balance_engine",
        None,
    )

    if ledger is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Balance ledger is not initialized.",
        )

    return ledger