FROM node:20-alpine

WORKDIR /app/llmops-dashboard

COPY llmops-dashboard/package.json llmops-dashboard/tsconfig.json llmops-dashboard/vite.config.ts llmops-dashboard/index.html /app/llmops-dashboard/
COPY llmops-dashboard/src /app/llmops-dashboard/src

RUN npm install
RUN npm run build

CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173"]
