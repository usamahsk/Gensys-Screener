FROM node:22-alpine AS builder
WORKDIR /app

# Remove ARG — do not bake the URL into the image
ENV NEXT_PUBLIC_API_URL=http://localhost:8100

COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

COPY . .
RUN corepack enable && pnpm build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]