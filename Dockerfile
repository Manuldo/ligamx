# Versión fijada: 22.23.4 corrige CVE-2026-111497 (Brotli RCE, oct 2026).
# Al actualizar, sube a la siguiente versión parcheada de la línea 22.
FROM node:22.23.4-alpine

ENV NODE_ENV=production
WORKDIR /app

COPY package*.json ./
RUN npm ci --omit=dev --no-audit --no-fund

COPY --chown=node:node . .
USER node

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/health/live').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
