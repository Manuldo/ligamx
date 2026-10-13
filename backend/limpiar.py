"""
limpiar.py — Herramienta para borrar la data de una liga y re-jalarla limpia.

USO:
    python limpiar.py ligamx

Primero te MUESTRA qué hay guardado en esa liga (para que veas la mezcla),
te pide confirmar escribiendo "BORRAR", y recién entonces borra todo.

Requiere la variable de entorno MONGO_URI (la misma que usa el backend).
"""

import os
import sys
from pymongo import MongoClient

# Colecciones que guardan data por liga
COLECCIONES = [
    "partidos", "jugadores", "clasificacion",
    "analisis", "picks_sugeridos", "boletos",
]


def main():
    if len(sys.argv) < 2:
        print("Uso: python limpiar.py <slug_liga>")
        print("Ejemplo: python limpiar.py ligamx")
        sys.exit(1)

    liga = sys.argv[1].strip()

    uri = os.environ.get("MONGO_URI")
    if not uri:
        print("❌ Falta la variable MONGO_URI.")
        print("   Ponla así antes de correr el script:")
        print('   Windows:  set MONGO_URI=mongodb+srv://...')
        print('   Mac/Linux: export MONGO_URI="mongodb+srv://..."')
        sys.exit(1)

    cliente = MongoClient(uri)
    db = cliente["apuestas_rag"]

    # 1. MOSTRAR qué hay
    print(f"\n📊 Data actual de la liga '{liga}':")
    print("─" * 40)
    total = 0
    conteos = {}
    for col in COLECCIONES:
        n = db[col].count_documents({"liga": liga})
        conteos[col] = n
        total += n
        print(f"  {col:20} {n:>6} documentos")
    print("─" * 40)
    print(f"  {'TOTAL':20} {total:>6} documentos\n")

    # mostrar los equipos guardados (para ver la mezcla)
    equipos = db.clasificacion.distinct("equipo", {"liga": liga})
    if equipos:
        print(f"🏟  Equipos en '{liga}' ({len(equipos)}):")
        for e in sorted(equipos):
            print(f"     · {e}")
        print()

    if total == 0:
        print("✅ No hay nada que borrar en esta liga.")
        return

    # 2. CONFIRMAR
    print("⚠️  Esto BORRARÁ toda esa data. Tendrás que re-jalarla con la extensión.")
    resp = input('   Escribe "BORRAR" para confirmar (o Enter para cancelar): ').strip()
    if resp != "BORRAR":
        print("Cancelado. No se borró nada.")
        return

    # 3. BORRAR
    print("\n🗑  Borrando...")
    for col in COLECCIONES:
        r = db[col].delete_many({"liga": liga})
        print(f"  {col:20} {r.deleted_count:>6} borrados")

    print(f"\n✅ Liga '{liga}' limpia. Ahora re-jálala con la extensión.\n")


if __name__ == "__main__":
    main()
