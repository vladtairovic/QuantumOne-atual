FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG OPTIMIZATION_TIMEOUT_SECONDS=60
ARG API_TIMEOUT_SECONDS=60
ARG ANALISE_TIMEOUT_SECONDS=900
ENV OPTIMIZATION_TIMEOUT_SECONDS=$OPTIMIZATION_TIMEOUT_SECONDS API_TIMEOUT_SECONDS=$API_TIMEOUT_SECONDS ANALISE_TIMEOUT_SECONDS=$ANALISE_TIMEOUT_SECONDS
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000 NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
