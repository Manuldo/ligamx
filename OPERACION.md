# Operación recomendada del motor Pickazo

## Producción

Usa estas variables:

- `ENVIRONMENT=production`
- `ENABLE_INTERNAL_UI=false`
- `ENABLE_INTERNAL_SCHEDULER=false`
- `WEB_CONCURRENCY=1` mientras Chroma siga en disco local.

La cara pública debe llamar exclusivamente a las rutas `/pickazo/*` con `PICKAZO_BRIDGE_KEY`. No expongas `API_TOKEN` al navegador.

## Jobs automáticos

Ejecuta comandos separados, que terminan al concluir:

```bash
python jobs.py setup-db
python jobs.py alineaciones
python jobs.py marcadores
```

Programación sugerida:

- `alineaciones`: cada 30 minutos.
- `marcadores`: cada 5 minutos; el comando no consulta API-Football cuando no hay partidos en ventana.

Los jobs usan un bloqueo en Mongo para prevenir ejecuciones simultáneas.

## Verificaciones después de desplegar

```bash
curl https://TU-MOTOR/salud
curl https://TU-MOTOR/pickazo/estado
```

Después prueba una llamada privada desde Node y confirma que una llamada sin `Authorization` reciba `401`.
