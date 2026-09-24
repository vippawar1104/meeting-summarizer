from fastapi import APIRouter, Request, Response
from sqlalchemy import text

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, str]:
    checks: dict[str, str] = {}
    try:
        async with request.app.state.sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "down"
    try:
        await request.app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "down"
    if "down" in checks.values():
        response.status_code = 503
    return checks
