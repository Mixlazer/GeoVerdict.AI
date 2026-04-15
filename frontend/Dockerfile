FROM node:20-alpine

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json* frontend/tsconfig.json frontend/next.config.mjs frontend/next-env.d.ts /app/frontend/
RUN npm install

COPY frontend /app/frontend

RUN npm run build

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
