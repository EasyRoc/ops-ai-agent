from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.crud import get_incident, list_incidents, AsyncSessionLocal

router = APIRouter(prefix="/api/v1")


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/incidents")
async def list_incidents_endpoint(status: str = None, limit: int = 50, db: AsyncSession = Depends(get_db)):
    incidents = await list_incidents(db, status=status, limit=limit)
    return {
        "total": len(incidents),
        "incidents": [
            {
                "id": i.id,
                "service": i.service,
                "env": i.env,
                "severity": i.severity,
                "status": i.status,
                "alert_name": i.alert_name,
                "root_cause": i.root_cause,
                "confidence": i.confidence,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in incidents
        ],
    }


@router.get("/incidents/{incident_id}")
async def get_incident_endpoint(incident_id: str, db: AsyncSession = Depends(get_db)):
    incident = await get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    return {
        "id": incident.id,
        "service": incident.service,
        "env": incident.env,
        "severity": incident.severity,
        "status": incident.status,
        "alert_name": incident.alert_name,
        "alert_value": incident.alert_value,
        "root_cause": incident.root_cause,
        "confidence": incident.confidence,
        "evidence": incident.evidence,
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
        "updated_at": incident.updated_at.isoformat() if incident.updated_at else None,
    }
