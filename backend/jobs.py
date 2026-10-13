"""Comandos de una sola ejecución para Railway Cron o GitHub Actions."""
import sys
from datetime import datetime, timezone, timedelta
from pymongo.errors import DuplicateKeyError

from db.mongo import get_db, crear_indices
from models.alineaciones import (
    actualizar_alineaciones_proximas,
    actualizar_marcadores_vivo,
    hay_api_key,
)


def acquire_lock(db, name: str, minutes: int = 20) -> bool:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=minutes)
    try:
        db.job_locks.insert_one({"_id": name, "expiresAt": expires, "createdAt": now})
        return True
    except DuplicateKeyError:
        result = db.job_locks.update_one(
            {"_id": name, "expiresAt": {"$lt": now}},
            {"$set": {"expiresAt": expires, "createdAt": now}},
        )
        return result.modified_count == 1


def release_lock(db, name: str):
    db.job_locks.delete_one({"_id": name})


def hay_partidos_en_vivo(db) -> bool:
    now = datetime.now(timezone.utc)
    return db.partidos.count_documents({
        "inicio": {
            "$gte": (now - timedelta(hours=3)).isoformat(),
            "$lte": now.isoformat(),
        },
        "estado": {"$ne": "finalizado"},
    }) > 0 or db.partidos.count_documents({"estado": "en_vivo"}) > 0


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python jobs.py setup-db|alineaciones|marcadores")
    job = sys.argv[1]
    db = get_db()

    if job == "setup-db":
        crear_indices()
        print({"ok": True, "job": job})
        return
    if not hay_api_key():
        raise SystemExit("Falta APIFOOTBALL_KEY")
    if not acquire_lock(db, job):
        print({"ok": True, "job": job, "omitido": "ya hay una ejecución activa"})
        return
    try:
        if job == "alineaciones":
            result = actualizar_alineaciones_proximas(db, float(__import__('os').environ.get("ALINEACIONES_VENTANA_H", "1.5")))
        elif job == "marcadores":
            result = actualizar_marcadores_vivo(db) if hay_partidos_en_vivo(db) else {"ok": True, "omitido": "sin partidos en ventana"}
        else:
            raise SystemExit(f"Job desconocido: {job}")
        print(result)
    finally:
        release_lock(db, job)


if __name__ == "__main__":
    main()
