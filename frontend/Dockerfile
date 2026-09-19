# syntax=docker/dockerfile:1
FROM node:22-alpine AS deps
WORKDIR /srv
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /srv
COPY --from=deps /srv/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /srv
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1

# Do not run as root (spec §31).
RUN addgroup -g 10001 -S nodejs && adduser -u 10001 -S nextjs -G nodejs

COPY --from=builder /srv/public ./public
COPY --from=builder --chown=nextjs:nodejs /srv/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /srv/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
