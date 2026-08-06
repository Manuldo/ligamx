# Seguridad operativa

- Nunca publiques `.env`, `ADMIN_KEY`, `MOTOR_KEY`, tokens de Stripe ni cadenas de MongoDB.
- En producción define `NODE_ENV=production`; el servidor fallará si faltan las variables críticas.
- Protege `admin.html` con Cloudflare Access o una capa equivalente. La llave administrativa no debe compartirse.
- Rota `JWT_SECRET`, `ADMIN_KEY` y `MOTOR_KEY` ante cualquier exposición.
- Configura el webhook de Stripe y verifica `/health/ready` después de cada despliegue.
