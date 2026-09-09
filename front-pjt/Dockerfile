# 1단계: Vite 빌드. VITE_API_BASE_URL 은 빌드 시점에 번들에 박히므로 ARG 로 받는다.
# nginx 가 /api 를 backend 로 프록시하므로 기본값은 상대 경로 /api 다 (도메인 무관).
FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .

ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# 2단계: nginx 가 정적 파일을 서빙하고 /api 를 backend 로 넘긴다.
FROM nginx:1.27-alpine

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1/"]
