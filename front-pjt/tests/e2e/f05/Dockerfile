FROM node:22-bookworm AS dependencies

WORKDIR /workspace/front-pjt
COPY front-pjt/package.json front-pjt/package-lock.json ./
RUN npm ci

FROM dependencies AS front

COPY front-pjt ./
COPY contract-fixtures /workspace/contract-fixtures

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

FROM dependencies AS e2e

RUN npx playwright install --with-deps chromium
COPY front-pjt ./
COPY contract-fixtures /workspace/contract-fixtures
CMD ["npm", "run", "test:e2e:f05"]
