# App iOS de Pickazo MX (Capacitor + notificaciones push)

La app iOS es un contenedor nativo (Capacitor) que **empaqueta la interfaz**
(`public/` → `www-app/` con `npm run build:app`) y llama a la API del servidor
(`APP_API_BASE`). El frontend es el mismo `public/index.html`; no hay lógica de
negocio duplicada. Ver `APP_STORE.md` para el flujo de build y envío.

## 1. Apple Developer (una sola vez)
1. **Identifiers → +** → *App IDs* → App → Bundle ID explícito `mx.pickazo.app`
   (si prefieres otro, cámbialo en `capacitor.config.json` y en `APNS_BUNDLE_ID`).
   Activa la capacidad **Push Notifications**.
2. **Keys → +** → marca **Apple Push Notifications service (APNs)** → descarga el `.p8`
   (solo se puede bajar una vez). Anota el **Key ID** y tu **Team ID**.

## 2. En la Mac
```bash
git fetch origin && git checkout rediseno-carbon
npm install
npx cap add ios        # crea la carpeta ios/ (solo la primera vez)
npx cap sync ios
npx cap open ios       # abre Xcode
```
En Xcode, target **App**:
- *Signing & Capabilities* → Team = tu cuenta, Bundle Identifier = `mx.pickazo.app`.
- **+ Capability → Push Notifications** y **Background Modes → Remote notifications**.
- Corre en un iPhone real (el push no funciona en simulador).

## 3. Variables en Railway (servicio que atiende a la app)
| Variable | Valor |
|---|---|
| `APNS_KEY_ID` | Key ID de la llave .p8 |
| `APNS_TEAM_ID` | Team ID |
| `APNS_KEY` | contenido completo del .p8 (puede ir con `\n`) |
| `APNS_BUNDLE_ID` | `mx.pickazo.app` |
| `APNS_SANDBOX` | `true` mientras pruebes desde Xcode/desarrollo; quítala para TestFlight/App Store |

Sin estas variables el push queda apagado y nada más se rompe.

## 4. Probar
1. Abre la app en el iPhone, inicia sesión y acepta las notificaciones.
2. Manda una prueba:
```bash
curl -X POST https://TU_URL/api/push/test \
  -H "x-admin-key: TU_ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"email":"tu@correo.com","titulo":"Pickazo MX","cuerpo":"Prueba de push"}'
```

## Notas
- Para probar con el preview, cambia `server.url` por la URL del preview de Railway y corre `npx cap sync ios`.
- Los pagos PRO dentro de la app: Apple exige In-App Purchase para suscripciones digitales; Stripe dentro de la app puede causar rechazo. Decidir antes de enviar a revisión.
